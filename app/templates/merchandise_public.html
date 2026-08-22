<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{{ title or 'BeatHub Merch' }} · BeatHub</title>
    <meta name="description" content="BeatHub merchandise">

    <style>
        :root {
            color-scheme: dark;
            --bg: #070709;
            --bg-soft: #0b0b0f;
            --panel: #111116;
            --panel-2: #17171d;
            --panel-3: #1c1c23;
            --text: #f7f7f8;
            --muted: #a6a6b0;
            --muted-2: #777783;
            --line: #292932;
            --line-soft: #202027;
            --white: #ffffff;
            --black: #08080a;
            --danger: #ff8c8c;
            --danger-bg: #281313;
            --success: #a7f3d0;
        }

        * {
            box-sizing: border-box;
        }

        html {
            scroll-behavior: smooth;
        }

        body {
            margin: 0;
            min-height: 100vh;
            background:
                radial-gradient(
                    circle at 15% 5%,
                    rgba(255,255,255,.055),
                    transparent 30%
                ),
                radial-gradient(
                    circle at 90% 20%,
                    rgba(255,255,255,.035),
                    transparent 28%
                ),
                linear-gradient(
                    180deg,
                    #070709 0%,
                    #09090c 45%,
                    #070709 100%
                );
            color: var(--text);
            font-family:
                Inter,
                ui-sans-serif,
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        a {
            color: inherit;
            text-decoration: none;
        }

        button,
        input,
        textarea {
            font: inherit;
        }

        .nav {
            position: sticky;
            top: 0;
            z-index: 50;
            background: rgba(7,7,9,.86);
            backdrop-filter: blur(18px);
            -webkit-backdrop-filter: blur(18px);
            border-bottom: 1px solid rgba(255,255,255,.08);
        }

        .nav-inner {
            max-width: 1220px;
            margin: 0 auto;
            min-height: 72px;
            padding: 0 24px;
            display: flex;
            align-items: center;
            gap: 28px;
        }

        .brand {
            font-size: 15px;
            font-weight: 950;
            letter-spacing: .16em;
            white-space: nowrap;
        }

        .links {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 22px;
            color: #cfcfd7;
            font-size: 13px;
            font-weight: 650;
            flex-wrap: wrap;
        }

        .links a {
            transition:
                color .2s ease,
                opacity .2s ease;
        }

        .links a:hover {
            color: #fff;
        }

        .wrap {
            width: 100%;
            max-width: 1220px;
            margin: 0 auto;
            padding: 54px 24px 90px;
        }

        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: #9c9ca7;
            font-size: 11px;
            line-height: 1;
            text-transform: uppercase;
            letter-spacing: .18em;
            font-weight: 850;
            margin-bottom: 12px;
        }

        .eyebrow::before {
            content: "";
            width: 22px;
            height: 1px;
            background: #777783;
        }

        h1,
        h2,
        p {
            margin-top: 0;
        }

        h1 {
            font-size: clamp(32px, 5vw, 58px);
            line-height: .98;
            letter-spacing: -.045em;
            margin-bottom: 14px;
        }

        h2 {
            font-size: 25px;
            line-height: 1.1;
            letter-spacing: -.025em;
            margin-bottom: 9px;
        }

        p {
            color: var(--muted);
            line-height: 1.65;
        }

        /* ============================================================
           PRODUCT DETAIL
           ============================================================ */

        .product {
            display: grid;
            grid-template-columns:
                minmax(0, 1.08fr)
                minmax(360px, .92fr);
            gap: 38px;
            align-items: start;
            margin-top: 20px;
        }

        .visual-column {
            min-width: 0;
        }

        .product-image {
            position: relative;
            width: 100%;
            aspect-ratio: 1 / 1;
            overflow: hidden;
            border-radius: 30px;
            border: 1px solid rgba(255,255,255,.10);
            background:
                radial-gradient(
                    circle at 50% 35%,
                    #24242b 0%,
                    #15151b 42%,
                    #0d0d11 100%
                );
            box-shadow:
                0 30px 80px rgba(0,0,0,.45),
                inset 0 1px 0 rgba(255,255,255,.04);
        }

        .product-image::after {
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(
                    135deg,
                    rgba(255,255,255,.08),
                    transparent 24%,
                    transparent 72%,
                    rgba(255,255,255,.025)
                );
        }

        .product-image img {
            position: relative;
            z-index: 1;
            display: block;
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: center;
            transition:
                transform .45s ease,
                opacity .25s ease;
        }

        .product-image:hover img {
            transform: scale(1.025);
        }

        .image-fallback {
            position: absolute;
            inset: 0;
            z-index: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            gap: 12px;
            color: #777783;
            text-align: center;
        }

        .image-fallback-icon {
            width: 74px;
            height: 74px;
            border-radius: 22px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(255,255,255,.045);
            border: 1px solid rgba(255,255,255,.08);
            font-size: 31px;
        }

        .image-fallback-text {
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .04em;
        }

        .image-badge {
            position: absolute;
            z-index: 3;
            left: 18px;
            bottom: 18px;
            display: inline-flex;
            align-items: center;
            gap: 7px;
            padding: 9px 12px;
            border-radius: 999px;
            background: rgba(7,7,9,.78);
            border: 1px solid rgba(255,255,255,.10);
            color: #e5e5ea;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            font-size: 11px;
            font-weight: 800;
        }

        .image-badge-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #fff;
        }

        .panel {
            position: sticky;
            top: 94px;
            min-width: 0;
            padding: 30px;
            border-radius: 28px;
            background:
                linear-gradient(
                    180deg,
                    rgba(24,24,30,.98),
                    rgba(15,15,20,.98)
                );
            border: 1px solid rgba(255,255,255,.09);
            box-shadow:
                0 25px 70px rgba(0,0,0,.32),
                inset 0 1px 0 rgba(255,255,255,.035);
        }

        .creator {
            color: #92929d;
            font-size: 13px;
            line-height: 1.5;
            margin-bottom: 10px;
        }

        .creator strong {
            color: #ededf0;
            font-weight: 850;
        }

        .product-title {
            margin-bottom: 13px;
        }

        .description {
            max-width: 600px;
            margin-bottom: 18px;
            font-size: 14px;
        }

        .price {
            display: flex;
            align-items: baseline;
            gap: 7px;
            margin: 4px 0 25px;
            color: #fff;
            font-size: 28px;
            font-weight: 950;
            letter-spacing: -.025em;
        }

        .price-currency {
            color: #a8a8b2;
            font-size: 12px;
            font-weight: 750;
            letter-spacing: .08em;
            text-transform: uppercase;
        }

        .checkout-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 15px;
            padding-top: 22px;
            border-top: 1px solid var(--line);
            margin-bottom: 2px;
        }

        .checkout-heading strong {
            font-size: 14px;
            font-weight: 900;
        }

        .checkout-heading span {
            color: #7e7e89;
            font-size: 11px;
            font-weight: 700;
        }

        .form-row {
            display: grid;
            grid-template-columns: 1fr 1.4fr;
            gap: 12px;
        }

        label {
            display: block;
            margin: 16px 0 7px;
            color: #dedee5;
            font-size: 12px;
            font-weight: 850;
            letter-spacing: .01em;
        }

        .optional {
            color: #777783;
            font-weight: 600;
        }

        input,
        textarea {
            width: 100%;
            border: 1px solid #303039;
            background: #09090d;
            color: #fff;
            border-radius: 14px;
            padding: 14px 15px;
            outline: none;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.02);
            transition:
                border-color .2s ease,
                background .2s ease,
                box-shadow .2s ease;
        }

        input {
            min-height: 50px;
        }

        textarea {
            min-height: 96px;
            resize: vertical;
            line-height: 1.5;
        }

        input::placeholder,
        textarea::placeholder {
            color: #5f5f69;
        }

        input:focus,
        textarea:focus {
            border-color: #686873;
            background: #0c0c11;
            box-shadow:
                0 0 0 3px rgba(255,255,255,.045);
        }

        .hint {
            margin-top: 7px;
            color: #777783;
            font-size: 11px;
            line-height: 1.5;
        }

        .notice {
            padding: 13px 15px;
            margin: 17px 0;
            border: 1px solid #34343d;
            border-radius: 14px;
            background: #141419;
            color: #d8d8df;
            font-size: 13px;
            line-height: 1.55;
        }

        .notice.error {
            border-color: #633737;
            background: var(--danger-bg);
            color: #ffc0c0;
        }

        .summary {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            margin-top: 22px;
            padding: 18px 0;
            border-top: 1px solid var(--line);
        }

        .summary-label {
            color: #a4a4ae;
            font-size: 13px;
            font-weight: 750;
        }

        .summary-total {
            color: #fff;
            font-size: 21px;
            font-weight: 950;
        }

        .btn {
            display: inline-flex;
            width: 100%;
            min-height: 52px;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 0 20px;
            border: 0;
            border-radius: 15px;
            background: #fff;
            color: #09090b;
            font-size: 13px;
            font-weight: 950;
            cursor: pointer;
            box-shadow:
                0 10px 25px rgba(0,0,0,.18);
            transition:
                transform .18s ease,
                opacity .18s ease,
                box-shadow .18s ease;
        }

        .btn:hover {
            opacity: .94;
            transform: translateY(-1px);
            box-shadow:
                0 14px 30px rgba(0,0,0,.25);
        }

        .btn:active {
            transform: translateY(0);
        }

        .mpesa-icon {
            width: 25px;
            height: 25px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            background: #09090b;
            color: #fff;
            font-size: 11px;
            font-weight: 950;
        }

        .delivery {
            display: flex;
            gap: 12px;
            margin-top: 18px;
            padding: 16px;
            border-radius: 16px;
            background: #111116;
            border: 1px solid #292932;
            color: #aaaab4;
            font-size: 12px;
            line-height: 1.6;
        }

        .delivery-icon {
            flex: 0 0 auto;
            width: 34px;
            height: 34px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #1b1b21;
            border: 1px solid #303039;
            font-size: 15px;
        }

        .delivery strong {
            display: block;
            color: #e5e5e9;
            font-size: 12px;
            margin-bottom: 2px;
        }

        .back-link {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            margin-top: 22px;
            color: #9696a1;
            font-size: 12px;
            font-weight: 750;
            transition: color .2s ease;
        }

        .back-link:hover {
            color: #fff;
        }

        /* ============================================================
           MARKETPLACE
           ============================================================ */

        .market-header {
            max-width: 760px;
        }

        .grid {
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(240px, 1fr)
                );
            gap: 22px;
            margin-top: 32px;
        }

        .card {
            overflow: hidden;
            border-radius: 22px;
            background: var(--panel);
            border: 1px solid var(--line);
            box-shadow: 0 18px 45px rgba(0,0,0,.22);
            transition:
                transform .22s ease,
                border-color .22s ease;
        }

        .card:hover {
            transform: translateY(-3px);
            border-color: #393943;
        }

        .image-box {
            position: relative;
            aspect-ratio: 1 / 1;
            overflow: hidden;
            background:
                linear-gradient(
                    135deg,
                    #19191f,
                    #0d0d11
                );
        }

        .image-box img {
            width: 100%;
            height: 100%;
            display: block;
            object-fit: cover;
            transition: transform .35s ease;
        }

        .card:hover .image-box img {
            transform: scale(1.025);
        }

        .fallback {
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 48px;
            opacity: .3;
        }

        .body {
            padding: 20px;
        }

        .body .creator {
            margin-bottom: 8px;
        }

        .body h2 {
            margin-bottom: 8px;
        }

        .body p {
            margin-bottom: 0;
            font-size: 13px;
        }

        .body .price {
            margin: 15px 0;
            font-size: 20px;
        }

        .body .btn {
            min-height: 44px;
        }

        /* ============================================================
           FOOTER
           ============================================================ */

        footer {
            max-width: 1220px;
            margin: 0 auto;
            padding: 22px 24px 38px;
            border-top: 1px solid rgba(255,255,255,.07);
            color: #6f6f79;
            font-size: 12px;
        }

        /* ============================================================
           MOBILE
           ============================================================ */

        @media (max-width: 900px) {
            .product {
                grid-template-columns: 1fr;
                gap: 22px;
            }

            .panel {
                position: static;
            }

            .product-image {
                max-width: 760px;
                margin: 0 auto;
            }
        }

        @media (max-width: 700px) {
            .nav-inner {
                min-height: auto;
                padding: 16px 18px;
                gap: 15px;
            }

            .brand {
                width: 100%;
            }

            .links {
                width: 100%;
                justify-content: flex-start;
                gap: 13px 18px;
            }

            .wrap {
                padding:
                    32px 18px
                    65px;
            }

            .product {
                margin-top: 10px;
            }

            .product-image {
                border-radius: 22px;
            }

            .panel {
                padding: 22px;
                border-radius: 22px;
            }

            .form-row {
                grid-template-columns: 1fr;
                gap: 0;
            }

            h1 {
                font-size: 39px;
            }

            .grid {
                grid-template-columns: 1fr;
            }
        }

        @media (max-width: 420px) {
            .links {
                font-size: 12px;
                gap: 11px 14px;
            }

            .panel {
                padding: 19px;
            }

            .product-image {
                border-radius: 18px;
            }

            .summary-total {
                font-size: 19px;
            }
        }
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
                <a href="/logout">Logout</a>
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

        <div class="visual-column">

            <div class="product-image" id="product-image-box">

                {% if product.image_url %}

                    <img
                        id="product-image"
                        src="{{ product.image_url }}"
                        alt="{{ product.name }}"
                        loading="eager"
                        decoding="async"
                        onerror="handleProductImageError(this)"
                    >

                    <div
                        class="image-fallback"
                        id="product-image-fallback"
                        style="display:none"
                    >
                        <div class="image-fallback-icon">
                            🛍️
                        </div>

                        <div class="image-fallback-text">
                            Product image unavailable
                        </div>
                    </div>

                {% else %}

                    <div class="image-fallback">
                        <div class="image-fallback-icon">
                            🛍️
                        </div>

                        <div class="image-fallback-text">
                            Product image unavailable
                        </div>
                    </div>

                {% endif %}

                <div class="image-badge">
                    <span class="image-badge-dot"></span>
                    BeatHub Merchandise
                </div>

            </div>

        </div>


        <section class="panel">

            <div class="creator">
                Merchandise from
                <strong>
                    {{ creator.stage_name if creator else 'BeatHub Creator' }}
                </strong>
            </div>

            <h1 class="product-title">
                {{ product.name }}
            </h1>

            <p class="description">
                {{ product.description or 'A physical BeatHub merchandise item.' }}
            </p>

            <div class="price">
                <span class="price-currency">KSh</span>
                {{ '%.2f'|format(product.price|float) }}
            </div>


            {% if query_error %}
                <div class="notice error">
                    {{ query_error }}
                </div>
            {% endif %}


            <div class="checkout-heading">
                <strong>Complete your purchase</strong>
                <span>Secure M-Pesa checkout</span>
            </div>


            {% if current_user %}

                <form
                    method="post"
                    action="/merch/{{ product.slug }}/buy"
                    id="merch-checkout-form"
                >

                    <div class="form-row">

                        <div>
                            <label for="quantity">
                                Quantity
                            </label>

                            <input
                                id="quantity"
                                name="quantity"
                                type="number"
                                min="1"
                                max="20"
                                value="1"
                                inputmode="numeric"
                                required
                            >
                        </div>


                        <div>
                            <label for="phone">
                                M-Pesa phone
                            </label>

                            <input
                                id="phone"
                                name="phone"
                                type="tel"
                                inputmode="numeric"
                                autocomplete="tel"
                                placeholder="0712 345 678"
                                required
                            >
                        </div>

                    </div>


                    <label for="order_note">
                        Order note
                        <span class="optional">
                            (optional)
                        </span>
                    </label>

                    <textarea
                        id="order_note"
                        name="order_note"
                        maxlength="300"
                        placeholder="Size, colour, delivery instructions, etc."
                    ></textarea>

                    <div class="hint">
                        Example: Size M · Black · Please call before delivery.
                    </div>


                    <div class="summary">
                        <span class="summary-label">
                            Total
                        </span>

                        <span
                            class="summary-total"
                            id="total"
                        >
                            KSh {{ '%.2f'|format(product.price|float) }}
                        </span>
                    </div>


                    <button
                        class="btn"
                        type="submit"
                        id="buy-button"
                    >
                        <span class="mpesa-icon">M</span>
                        <span>Buy Merchandise with M-Pesa</span>
                    </button>


                    <div class="delivery">

                        <div class="delivery-icon">
                            📦
                        </div>

                        <div>
                            <strong>
                                Physical merchandise
                            </strong>

                            After payment, your order is queued for fulfilment.
                            Delivery time varies depending on your location
                            and the creator's fulfilment process.
                        </div>

                    </div>

                </form>

            {% else %}

                <div class="notice">
                    Please
                    <a
                        href="/login"
                        style="text-decoration:underline;font-weight:800"
                    >
                        log in
                    </a>
                    to purchase this merchandise.
                </div>

            {% endif %}


            {% if store_url %}

                <a
                    class="back-link"
                    href="{{ store_url }}"
                >
                    <span>←</span>
                    <span>Back to creator store</span>
                </a>

            {% else %}

                <a
                    class="back-link"
                    href="/merch"
                >
                    <span>←</span>
                    <span>Browse merchandise</span>
                </a>

            {% endif %}

        </section>

    </div>


    <script>
        (function () {

            const quantity =
                document.getElementById("quantity");

            const total =
                document.getElementById("total");

            const buyButton =
                document.getElementById("buy-button");

            const form =
                document.getElementById("merch-checkout-form");

            const unitPrice =
                Number({{ product.price|float }});


            function updateTotal() {

                if (!quantity || !total) {
                    return;
                }

                let value =
                    parseInt(
                        quantity.value || "1",
                        10
                    );

                if (!Number.isFinite(value)) {
                    value = 1;
                }

                if (value < 1) {
                    value = 1;
                }

                if (value > 20) {
                    value = 20;
                }

                quantity.value = value;

                total.textContent =
                    "KSh " +
                    (unitPrice * value).toFixed(2);
            }


            if (quantity) {
                quantity.addEventListener(
                    "input",
                    updateTotal
                );

                quantity.addEventListener(
                    "change",
                    updateTotal
                );
            }


            if (form && buyButton) {

                form.addEventListener(
                    "submit",
                    function () {

                        buyButton.disabled = true;

                        buyButton.style.opacity = "0.65";

                        buyButton.style.cursor =
                            "wait";

                        buyButton.innerHTML =
                            '<span class="mpesa-icon">M</span>' +
                            '<span>Starting M-Pesa payment...</span>';
                    }
                );
            }


            updateTotal();

        })();


        function handleProductImageError(image) {

            if (!image) {
                return;
            }

            const fallback =
                document.getElementById(
                    "product-image-fallback"
                );

            if (fallback) {
                fallback.style.display = "flex";
            }

            image.style.display = "none";
        }
    </script>


{% else %}

    <div class="market-header">

        <div class="eyebrow">
            BeatHub
        </div>

        <h1>
            {{ title or 'BeatHub Merch' }}
        </h1>

        {% if creator %}

            <p>
                Official merchandise from
                <strong>
                    {{ creator.stage_name }}
                </strong>.
            </p>

        {% else %}

            <p>
                Discover merchandise from BeatHub creators.
            </p>

        {% endif %}

    </div>


    <div class="grid">

        {% for product in products %}

            <article class="card">

                <a href="/merch/{{ product.slug }}">

                    <div class="image-box">

                        {% if product.image_url %}

                            <img
                                src="{{ product.image_url }}"
                                alt="{{ product.name }}"
                                loading="lazy"
                                onerror="this.style.display='none';this.nextElementSibling.style.display='flex';"
                            >

                            <div
                                class="fallback"
                                style="display:none"
                            >
                                🛍️
                            </div>

                        {% else %}

                            <div class="fallback">
                                🛍️
                            </div>

                        {% endif %}

                    </div>

                </a>


                <div class="body">

                    {% if product.creator_name %}

                        <div class="creator">
                            {{ product.creator_name }}
                        </div>

                    {% endif %}


                    <h2>
                        {{ product.name }}
                    </h2>


                    <p>
                        {{ product.description or 'No description added yet.' }}
                    </p>


                    <div class="price">
                        <span class="price-currency">KSh</span>
                        {{ '%.2f'|format(product.price|float) }}
                    </div>


                    <a
                        class="btn"
                        href="/merch/{{ product.slug }}"
                    >
                        View Merchandise
                    </a>

                </div>

            </article>

        {% else %}

            <div class="notice">
                No merchandise is available yet.
            </div>

        {% endfor %}

    </div>

{% endif %}

</main>


<footer>
    © {{ current_year }} BeatHub. All rights reserved.
</footer>

</body>
</html>
