<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{{ title or 'BeatHub Merch' }} · BeatHub</title>
    <meta name="description" content="BeatHub merchandise">
    <style>
        :root{color-scheme:dark;--bg:#070709;--panel:#111116;--panel2:#17171d;--text:#f5f5f7;--muted:#a7a7b2;--line:#292932;--accent:#fff;--danger:#ff7b7b;}
        *{box-sizing:border-box}
        body{margin:0;background:linear-gradient(180deg,#070709 0%,#0b0b0f 100%);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        a{color:inherit;text-decoration:none}
        .nav{position:sticky;top:0;z-index:20;background:rgba(7,7,9,.9);backdrop-filter:blur(16px);border-bottom:1px solid var(--line)}
        .nav-inner{max-width:1180px;margin:auto;padding:16px 22px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}
        .brand{font-weight:900;letter-spacing:.08em;margin-right:auto}
        .links{display:flex;gap:16px;flex-wrap:wrap;color:#d0d0d8;font-size:14px}
        .links a:hover{color:#fff}
        .wrap{max-width:1180px;margin:auto;padding:38px 22px 70px}
        .eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:#8d8d99;font-weight:800}
        h1{font-size:clamp(30px,5vw,54px);line-height:1.02;margin:8px 0 12px}
        h2{margin:0 0 10px;font-size:26px}
        p{color:var(--muted);line-height:1.65}
        .notice{padding:13px 15px;border:1px solid #3a3a43;background:#15151a;border-radius:14px;margin:18px 0;color:#d9d9e1}
        .error{border-color:#633333;background:#241313;color:#ffb5b5}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin-top:28px}
        .card{background:var(--panel);border:1px solid var(--line);border-radius:22px;overflow:hidden;box-shadow:0 14px 40px rgba(0,0,0,.2)}
        .image-box{aspect-ratio:1/1;background:linear-gradient(135deg,#18181e,#0d0d11);display:flex;align-items:center;justify-content:center;overflow:hidden}
        .image-box img{width:100%;height:100%;object-fit:cover;display:block}
        .fallback{font-size:48px;opacity:.35}
        .body{padding:19px}
        .creator{font-size:13px;color:#92929e;margin-bottom:8px}
        .price{font-size:21px;font-weight:900;margin:14px 0}
        .btn{display:inline-flex;justify-content:center;align-items:center;min-height:46px;padding:0 18px;border-radius:13px;background:#fff;color:#09090b;font-weight:900;border:0;cursor:pointer}
        .btn:hover{opacity:.9}
        .product{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(320px,.95fr);gap:34px;align-items:start;margin-top:25px}
        .product-image{border-radius:24px;overflow:hidden;border:1px solid var(--line);background:#111116;aspect-ratio:1/1;display:flex;align-items:center;justify-content:center}
        .product-image img{width:100%;height:100%;object-fit:cover}
        .panel{background:var(--panel);border:1px solid var(--line);border-radius:24px;padding:25px}
        .form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
        label{display:block;font-size:13px;font-weight:800;margin:15px 0 7px;color:#dddde5}
        input,textarea{width:100%;border:1px solid #34343d;background:#0b0b0f;color:#fff;border-radius:12px;padding:13px 14px;font:inherit;outline:none}
        input:focus,textarea:focus{border-color:#777783}
        textarea{min-height:100px;resize:vertical}
        .hint{font-size:12px;color:#81818d;margin-top:7px;line-height:1.5}
        .summary{display:flex;justify-content:space-between;gap:20px;padding:15px 0;border-top:1px solid var(--line);margin-top:18px;font-weight:800}
        .delivery{margin-top:18px;padding:15px;border-radius:14px;background:#15151a;border:1px solid var(--line);font-size:13px;color:#bcbcc6;line-height:1.6}
        footer{max-width:1180px;margin:auto;padding:20px 22px 35px;border-top:1px solid var(--line);color:#777783;font-size:13px}
        @media(max-width:780px){.product{grid-template-columns:1fr}.form-row{grid-template-columns:1fr}.links{width:100%}.wrap{padding-top:26px}}
    </style>
</head>
<body>
<header class="nav">
    <div class="nav-inner">
        <a class="brand" href="/">BEATHUB</a>
        <nav class="links">
            <a href="/beats">Beats</a>
            <a href="/sessions">Sessions</a>
            <a href="/hot-picks">Hot Picks</a>
            <a href="/merch">Merch</a>
            <a href="/terms">Terms</a>
            {% if current_user %}
                <a href="/dashboard">Dashboard</a>
            {% else %}
                <a href="/login">Login</a>
            {% endif %}
        </nav>
    </div>
</header>

<main class="wrap">
{% if product_detail and products %}
    {% set product = products[0] %}
    <div class="eyebrow">BeatHub Merchandise</div>
    <div class="product">
        <div class="product-image">
            {% if product.image_url %}
                <img src="{{ product.image_url }}" alt="{{ product.name }}" loading="eager" onerror="this.style.display='none';this.nextElementSibling.style.display='block';">
                <div class="fallback" style="display:none">🛍️</div>
            {% else %}
                <div class="fallback">🛍️</div>
            {% endif %}
        </div>

        <section class="panel">
            <div class="creator">Merchandise from <strong>{{ creator.stage_name if creator else 'BeatHub Creator' }}</strong></div>
            <h1>{{ product.name }}</h1>
            <p>{{ product.description or 'A physical BeatHub merchandise item.' }}</p>
            <div class="price">KSh {{ '%.2f'|format(product.price|float) }}</div>

            {% if query_error %}
                <div class="notice error">{{ query_error }}</div>
            {% endif %}

            {% if current_user %}
            <form method="post" action="/merch/{{ product.slug }}/buy">
                <div class="form-row">
                    <div>
                        <label for="quantity">Quantity</label>
                        <input id="quantity" name="quantity" type="number" min="1" max="20" value="1" required>
                    </div>
                    <div>
                        <label for="phone">M-Pesa phone</label>
                        <input id="phone" name="phone" type="tel" inputmode="numeric" autocomplete="tel" placeholder="0712 345 678" required>
                    </div>
                </div>

                <label for="order_note">Order note <span style="color:#777783;font-weight:500">(optional)</span></label>
                <textarea id="order_note" name="order_note" maxlength="300" placeholder="Size, color, delivery instructions, etc."></textarea>
                <div class="hint">Example: Size M · Black · Please call before delivery.</div>

                <div class="summary">
                    <span>Total</span>
                    <span id="total">KSh {{ '%.2f'|format(product.price|float) }}</span>
                </div>

                <button class="btn" type="submit" style="width:100%">Buy Merchandise with M-Pesa</button>

                <div class="delivery">
                    <strong>Physical merchandise</strong><br>
                    After payment, your order is queued for fulfilment. Delivery time varies depending on your location and the creator's fulfilment process.
                </div>
            </form>
            {% else %}
                <div class="notice">Please <a href="/login" style="text-decoration:underline">log in</a> to purchase this merchandise.</div>
            {% endif %}

            {% if store_url %}
                <p style="margin-bottom:0"><a href="{{ store_url }}" style="text-decoration:underline">← Back to creator store</a></p>
            {% else %}
                <p style="margin-bottom:0"><a href="/merch" style="text-decoration:underline">← Browse merchandise</a></p>
            {% endif %}
        </section>
    </div>

    <script>
        (function(){
            const q=document.getElementById('quantity');
            const total=document.getElementById('total');
            const unit={{ product.price|float }};
            if(!q||!total)return;
            function update(){
                let n=parseInt(q.value||'1',10);
                if(!Number.isFinite(n)||n<1)n=1;
                if(n>20)n=20;
                q.value=n;
                total.textContent='KSh '+(unit*n).toFixed(2);
            }
            q.addEventListener('input',update);
            update();
        })();
    </script>
{% else %}
    <div class="eyebrow">BeatHub</div>
    <h1>{{ title or 'BeatHub Merch' }}</h1>
    {% if creator %}
        <p>Official merchandise from <strong>{{ creator.stage_name }}</strong>.</p>
    {% else %}
        <p>Discover merchandise from BeatHub creators.</p>
    {% endif %}

    <div class="grid">
    {% for product in products %}
        <article class="card">
            <a href="/merch/{{ product.slug }}">
                <div class="image-box">
                    {% if product.image_url %}
                        <img src="{{ product.image_url }}" alt="{{ product.name }}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='block';">
                        <div class="fallback" style="display:none">🛍️</div>
                    {% else %}
                        <div class="fallback">🛍️</div>
                    {% endif %}
                </div>
            </a>
            <div class="body">
                {% if product.creator_name %}<div class="creator">{{ product.creator_name }}</div>{% endif %}
                <h2>{{ product.name }}</h2>
                <p>{{ product.description or 'No description added yet.' }}</p>
                <div class="price">KSh {{ '%.2f'|format(product.price|float) }}</div>
                <a class="btn" href="/merch/{{ product.slug }}">View Merchandise</a>
            </div>
        </article>
    {% else %}
        <div class="notice">No merchandise is available yet.</div>
    {% endfor %}
    </div>
{% endif %}
</main>

<footer>© {{ current_year }} BeatHub. All rights reserved.</footer>
</body>
</html>
