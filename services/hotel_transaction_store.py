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
    def create(self, **d):
        with self.connect() as c:
            c.execute("""INSERT INTO trust_trip_hotel_transactions
            (id,customer_ref,provider,hotel_name,offer_json,prebook_json,holder_json,guests_json,amount_inr,razorpay_order_id,payment_status,booking_status)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)""",
            (d['id'],d.get('customer_ref'),d['provider'],d.get('hotel_name'),json.dumps(d['offer']),json.dumps(d['prebook']),json.dumps(d['holder']),json.dumps(d['guests']),d['amount_inr'],d.get('razorpay_order_id'),'created','awaiting_payment'))
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
