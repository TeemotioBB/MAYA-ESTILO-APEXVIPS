# 🤖 Maya Bot — Telegram Sales Bot

Bot Telegram replicando o fluxo do "Maya Oficial":
áudio inicial → 3 ofertas → seleção de plano → geração de PIX via **SyncPay** → liberação automática do canal VIP.

---

## 📂 Estrutura

```
maya_bot/
├── maya_bot.py         # Entrypoint — Flask + Telegram handlers
├── syncpay.py          # Integração SyncPay (PIX + webhook)
├── config.py           # Settings + planos
├── requirements.txt
├── .env.example
├── README.md
└── content/            # Coloque suas mídias aqui
    ├── photo_profile.jpg   (opcional)
    ├── audio_start.ogg     (opcional, voice message)
    └── video_teaser.mp4    (opcional)
```

---

## 🚀 Setup passo a passo

### 1. Pré-requisitos

- Python 3.11+
- Redis rodando local (`redis-server`) ou Redis Cloud
- URL pública pra o webhook da SyncPay (em dev: ngrok / cloudflared)
- Conta SyncPay com `client_id` e `client_secret`

### 2. Criar o bot no Telegram

1. No Telegram, abra `@BotFather`
2. `/newbot` → nome → username
3. Copie o `BOT_TOKEN` que ele te dá
4. (Opcional) `/setuserpic` pra colocar foto, `/setdescription`, `/setabouttext`

### 3. Criar os canais VIP

1. Crie um canal **privado** no Telegram (Novo Canal → Privado)
2. Adicione seu bot como administrador com permissão **"Convidar Usuários via Link"**
3. Pegue o ID do canal:
   - Encaminhe qualquer mensagem do canal pro bot `@userinfobot`
   - Ou use a API: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Formato: `-100xxxxxxxxxx`
4. Repita pro canal VIP+ (se quiser ter o plano upsell)

### 4. Instalar dependências

```bash
cd maya_bot
python -m venv venv
source venv/bin/activate           # Linux/Mac
# .\venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 5. Configurar `.env`

```bash
cp .env.example .env
nano .env       # ou seu editor favorito
```

Preencha **todas** as variáveis. `BOT_TOKEN`, `SYNCPAY_CLIENT_ID/SECRET`,
`WEBHOOK_BASE_URL` e pelo menos um `VIP_CHANNEL_ID` são obrigatórios.

### 6. Configurar o webhook na SyncPay

No painel da SyncPay, registre a URL:
```
https://seudominio.com/webhook/syncpay
```
(ou a URL do ngrok em dev)

### 7. (Opcional) Adicionar mídias

Coloque os arquivos em `content/`:
- `photo_profile.jpg` — foto que aparece antes do texto do /start
- `audio_start.ogg` — voice message inicial (.ogg recomendado pra aparecer com ondinha; .mp3 também funciona)
- `video_teaser.mp4` — vídeo de prévia mostrado antes do PIX

Se não tiver, o bot pula essas etapas sem quebrar.

### 8. Rodar

```bash
python maya_bot.py
```

Você deve ver:
```
[INFO] maya_bot: 🚀 Maya Bot rodando na porta 8080
[INFO] [SyncPay] ✅ Integração iniciada
```

---

## 🧪 Testar

1. No Telegram, abra seu bot e mande `/start`
2. Você deve receber: foto → voice → texto + 3 botões
3. Clique num plano → confirmação + "Pagar com Pix"
4. Clique em "Pagar com Pix" → vídeo + código PIX + 3 botões
5. Pague o PIX
6. Em alguns segundos: "Pagamento confirmado" + botão pro canal VIP

---

## 🔧 Customização

### Mudar copy / textos

Tudo concentrado em `maya_bot.py` nas funções:
- `welcome_text()` — texto do /start
- `plan_confirmation_text()` — após escolher plano
- `pix_intro_text()` — antes do código PIX
- `pix_instructions_text()` — abaixo do código

### Mudar planos / preços

Em `config.py`, edite o dict `PLANS` e a lista `PLANS_DISPLAY_ORDER`.

### Mudar o nome da persona

`BOT_PERSONA_NAME` no `.env`. O nome é injetado em todos os textos.

### Roteamento multi-persona (avançado)

Sua integração original tinha `get_router().get_ia_config(uid=uid)` pra
rodar várias personas no mesmo código. Pra adicionar isso aqui:
1. Crie um `personas.py` com dict `{persona_id: {welcome_text, plans, channels...}}`
2. No `/start`, decida a persona por start parameter ou por bot token
3. Passe `persona_id` adiante via `context.user_data`

---

## 🛡️ Segurança

**Críticas (faça antes de subir pra produção):**
- ✅ Credenciais SyncPay em env vars (já está)
- ⚠️ **Validar assinatura HMAC do webhook SyncPay** — a versão atual aceita qualquer POST. Veja na doc da SyncPay como verificar a signature e adicione em `_register_webhook_route` antes de processar.
- ⚠️ **Rate limit no /start** — sem isso dá pra esgotar sua geração de PIX. Adicione um `r.incr` com TTL de 60s.
- ⚠️ Rodar atrás de Nginx com HTTPS válido (Let's Encrypt). Webhook SyncPay só funciona em HTTPS.

---

## 📊 Próximos passos sugeridos

1. **Meta CAPI** — publicar eventos `Lead`, `InitiateCheckout`, `Purchase` no Redis pub/sub `apex:events` (sua arquitetura original já contemplava). Mantenho o snapshot do customer no `salvar_customer` exatamente pra isso.
2. **Anti-clone** — gerar `apx` único na LP (igual ApexVips) e validar no `/start` via start parameter.
3. **Remarketing** — cron job que varre quem clicou em plano mas não pagou em 30min e manda mensagem com novo PIX.
4. **Order bump / upsell** — após confirmar pagamento do plano "tudo", oferecer upgrade pro "tudo_plus" com desconto.
5. **Dashboard** — `/admin` no bot com stats: vendas hoje, conversão por plano, etc.

---

## ⚖️ Compliance

Conteúdo adulto entre adultos é legal no Brasil, mas:
- O Telegram pode banir bots com conteúdo NSFW dependendo da política vigente. Use canais privados (não públicos) pra reduzir risco.
- Considere uma tela de verificação de idade 18+ antes de mostrar os botões de oferta.
- Pra LGPD: o snapshot do customer no Redis tem dados pessoais (nome, username). Defina retenção (TTL já está em 2h pós-pagamento) e tenha política de privacidade.

---

## 🐛 Debug comum

| Sintoma | Causa provável |
|---|---|
| `/start` não responde | Token errado, ou polling não iniciou (cheque log) |
| PIX gera mas webhook não bate | `WEBHOOK_BASE_URL` errado, ou não configurou no painel SyncPay |
| "❌ Configure SYNCPAY_CLIENT_ID..." | `.env` não foi lido — confira se está na mesma pasta de `maya_bot.py` |
| `create_chat_invite_link` falha | Bot não é admin do canal, ou não tem permissão "Convidar Usuários" |
| Voice message não toca como áudio | Use `.ogg` (codec Opus) — `.mp3` aparece como arquivo |

---
