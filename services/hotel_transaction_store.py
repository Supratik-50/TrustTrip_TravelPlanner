from __future__ import annotations
import json, os
import psycopg
from psycopg.rows import dict_row

class HotelTransactionStore:
    def __init__(self):
        url=os.getenv("DATABASE_URL")
        if not url: raise RuntimeError("DATABASE_URL is required for hotel payment persistence.")
        if "sslmode=" not in url: url += ("&" if "?" in url else "?")+"sslmode=require"
        self.url=url
        self.setup()
    def connect(self): return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
    def setup(self):
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS trust_trip_hotel_transactions (
                id TEXT PRIMARY KEY, customer_ref TEXT, provider TEXT NOT NULL, hotel_name TEXT, offer_json JSONB NOT NULL,
                prebook_json JSONB NOT NULL, holder_json JSONB NOT NULL, guests_json JSONB NOT NULL,
                amount_inr NUMERIC(14,2) NOT NULL, currency TEXT NOT NULL DEFAULT 'INR',
                razorpay_order_id TEXT, razorpay_payment_id TEXT, payment_status TEXT NOT NULL DEFAULT 'pending',
                booking_status TEXT NOT NULL DEFAULT 'pending', booking_json JSONB, error TEXT, pdf_generated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS customer_ref TEXT")
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS pdf_generated_at TIMESTAMPTZ")
            # Section 10: payment transaction lineage fields
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS trip_id UUID")
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS revision_id UUID")
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS revision_number INT")
            c.execute("ALTER TABLE trust_trip_hotel_transactions ADD COLUMN IF NOT EXISTS payment_idempotency_key TEXT")
            c.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_trip_hotel_transactions_idempotency_key
                ON trust_trip_hotel_transactions (payment_idempotency_key)
                WHERE payment_idempotency_key IS NOT NULL
            """)
    def create(self, **d):
        with self.connect() as c:
            c.execute("""INSERT INTO trust_trip_hotel_transactions
            (id,customer_ref,provider,hotel_name,offer_json,prebook_json,holder_json,guests_json,amount_inr,razorpay_order_id,payment_status,booking_status,trip_id,revision_id,revision_number,payment_idempotency_key)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (d['id'],d.get('customer_ref'),d['provider'],d.get('hotel_name'),json.dumps(d['offer']),json.dumps(d['prebook']),json.dumps(d['holder']),json.dumps(d['guests']),d['amount_inr'],d.get('razorpay_order_id'),'created','awaiting_payment',d.get('trip_id'),d.get('revision_id'),d.get('revision_number'),d.get('payment_idempotency_key')))
    def reserve_payment_order(self, **d):
        with self.connect() as c:
            row = c.execute("""
                INSERT INTO trust_trip_hotel_transactions
                (id,customer_ref,provider,hotel_name,offer_json,prebook_json,holder_json,guests_json,
                 amount_inr,razorpay_order_id,payment_status,booking_status,trip_id,revision_id,
                 revision_number,payment_idempotency_key)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,NULL,
                        'created','awaiting_payment',%s,%s,%s,%s)
                ON CONFLICT (payment_idempotency_key)
                WHERE payment_idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING *
            """, (d['id'], d.get('customer_ref'), d['provider'], d.get('hotel_name'),
                  json.dumps(d['offer']), json.dumps(d['prebook']), json.dumps(d['holder']),
                  json.dumps(d['guests']), d['amount_inr'], d.get('trip_id'),
                  d.get('revision_id'), d.get('revision_number'), d['payment_idempotency_key'])).fetchone()
            if row:
                return row
            existing = c.execute(
                "SELECT * FROM trust_trip_hotel_transactions WHERE payment_idempotency_key=%s FOR UPDATE",
                (d['payment_idempotency_key'],),
            ).fetchone()
            if existing:
                def json_value(value):
                    if isinstance(value, str):
                        return json.loads(value)
                    return value
                same_request = (
                    str(existing.get('customer_ref') or '') == str(d.get('customer_ref') or '')
                    and str(existing.get('provider') or '') == str(d.get('provider') or '')
                    and str(existing.get('trip_id') or '') == str(d.get('trip_id') or '')
                    and str(existing.get('revision_id') or '') == str(d.get('revision_id') or '')
                    and int(existing.get('revision_number') or 0) == int(d.get('revision_number') or 0)
                    and round(float(existing.get('amount_inr') or 0), 2) == round(float(d.get('amount_inr') or 0), 2)
                    and json_value(existing.get('offer_json')) == d.get('offer')
                    and json_value(existing.get('holder_json')) == d.get('holder')
                    and json_value(existing.get('guests_json')) == d.get('guests')
                )
                if not same_request:
                    raise ValueError("payment_idempotency_key was already used with a different hotel payment request.")
            return existing
    def list_recent(self, limit=50, customer_ref=None):
        with self.connect() as c:
            if customer_ref:
                return c.execute("SELECT * FROM trust_trip_hotel_transactions WHERE customer_ref=%s ORDER BY created_at DESC LIMIT %s", (customer_ref, limit)).fetchall()
            return c.execute("SELECT * FROM trust_trip_hotel_transactions ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
    def delete(self, txid, customer_ref):
        with self.connect() as c:
            row=c.execute("DELETE FROM trust_trip_hotel_transactions WHERE id=%s AND customer_ref=%s RETURNING id", (txid, customer_ref)).fetchone()
            return bool(row)
    def get(self, txid):
        with self.connect() as c: return c.execute("SELECT * FROM trust_trip_hotel_transactions WHERE id=%s",(txid,)).fetchone()
    def get_by_payment_idempotency_key(self, key):
        with self.connect() as c:
            return c.execute("SELECT * FROM trust_trip_hotel_transactions WHERE payment_idempotency_key=%s", (key,)).fetchone()
    def claim_booking(self, txid: str, razorpay_payment_id: str | None = None) -> bool:
        """Atomically transition booking_status to 'booking_in_progress' via compare-and-set."""
        with self.connect() as c:
            row = c.execute("""
                UPDATE trust_trip_hotel_transactions
                   SET booking_status='booking_in_progress',
                       payment_status='captured',
                       razorpay_payment_id=COALESCE(%s, razorpay_payment_id),
                       updated_at=now()
                 WHERE id=%s
                   AND booking_status IN ('created', 'awaiting_payment', 'pending', 'payment_captured')
                RETURNING id
            """, (razorpay_payment_id, txid)).fetchone()
            return bool(row)
    def update(self, txid, **d):
        allowed={'razorpay_payment_id','payment_status','booking_status','booking_json','error','razorpay_order_id','pdf_generated_at'}
        d={k:v for k,v in d.items() if k in allowed}
        if not d:return
        vals=[]
        sets=[]
        for k,v in d.items():
            if k=='booking_json': sets.append(f'{k}=%s::jsonb'); vals.append(json.dumps(v))
            else: sets.append(f'{k}=%s'); vals.append(v)
        vals.append(txid)
        with self.connect() as c: c.execute(f"UPDATE trust_trip_hotel_transactions SET {', '.join(sets)}, updated_at=now() WHERE id=%s",vals)
