from app.routers import paystack_checkout as p
async def f(reference=None,trxref=None,db=p.Depends(p.get_db)):
 r=reference or trxref;x=p.PaymentTransaction;z=db.query(x).filter(x.checkout_request_id==r).first();o=db.get(p.Order,z.order_id)
 from app.routers.paystack_checkout import _complete_verified_payment as c,_verify_reference as v
 c(db,o,z,await v(r));return p.RedirectResponse(f'/track/{o.track.slug}',303)
for r in p.router.routes:
 if getattr(r,'path',0)=='/paystack/callback':r.endpoint=f
