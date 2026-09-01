from app.routers import checkout as c
async def f(reference=None,trxref=None,db=c.Depends(c.get_db)):
 r=reference or trxref;p=db.query(c.PaymentTransaction).filter(c.PaymentTransaction.checkout_request_id==r).first();o=p.order
 from app.routers.paystack_checkout import _complete_verified_payment as x,_verify_reference as v
 x(db,o,p,await v(r));return c.RedirectResponse(f'/track/{o.track.slug}',303)
for r in c.router.routes:
 if getattr(r,'path',0)=='/paystack/callback':r.endpoint=f
