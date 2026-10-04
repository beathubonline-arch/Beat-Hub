# Mkulima context-aware replies

Ordinary WhatsApp text is interpreted with GPT-6 Astra, using bounded recent
conversation turns and known farm facts. Existing commands, checkout and
marketplace actions remain in application code. Strict structured output allows
only farm facts, the current goal, language and a reply. Exact sale totals are
calculated by the server. Unknown phrases can trigger one contextual follow-up.

Set `MKULIMA_OPENAI_API_KEY` in the Mkulima Render service environment (or use
an existing `OPENAI_API_KEY`). Use an OpenAI API account with model access and
billing/credits. Never commit credentials or paste them into chat.
`MKULIMA_TEXT_MODEL` defaults to `gpt-6-astra`.

`/api/health` and `/api/readiness` report `text_ai.configured` and the model.
Configured means a key is present, not that provider access or billing has been
verified. Ordinary messages store only status/model-independent bounded history.
API requests use `store: false`, 25-second timeout, bounded input and output;
provider errors, invalid output and missing keys fall back to existing replies.
Repeated fallback prompts become clarification rather than identical loops.

No connected model means no claim of Astra-generated answers. A ChatGPT
subscription does not supply the server's API credentials. Provider outputs can
still be wrong; current prices, weather, transactions and definitive diagnoses
must not be inferred from model memory. Voice/video transcription is not part of
this change; those messages receive an honest text-description fallback.

Validation:
`python -m unittest tests.test_mkulima_conversation_ai tests.test_mkulima_sale_followups tests.test_mkulima_intent_engine tests.test_mkulima_state_store -v`

Provider tests use mocked HTTP, never charge an account. Webhook tests cover
natural corrections followed by a topic change and the original screenshot flow.
