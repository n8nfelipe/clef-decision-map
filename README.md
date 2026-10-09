# Decisões e trânsito com Cloudflare Clef

Aplicação prática de decisões estruturadas inspirada no JEV System One. O
projeto tem dois fluxos: classifica tickets de suporte e analisa o trânsito no
corredor Brasília-Valparaíso de Goiás. O Clef, modelo de decisão da Cloudflare,
recebe perguntas tipadas e retorna probabilidades para orientar a decisão; as
políticas operacionais continuam determinísticas no código.

> **Modelo:** `@cf/cloudflare/clef`, usando a API de decisão tipada do Workers AI.
> O Clef é o equivalente mais próximo ao fluxo do JEV: recebe um estado e um
> schema de perguntas (`noul`, `choice` e `score`) e retorna probabilidades por
> alternativa.

## Requisitos

- Python 3.10+
- Cloudflare Account ID
- API Token Cloudflare com permissão **Workers AI: Read**
- Chave da Google Maps Platform com a **Routes API** habilitada e billing ativo

## Configurar e executar

```bash
cp .env.example .env
# preencha as variáveis obrigatórias no .env
python3 app.py --msg "Fui cobrado duas vezes e preciso do estorno hoje"
```

Variáveis:

- `CLOUDFLARE_ACCOUNT_ID`: identificador da conta Cloudflare.
- `CLOUDFLARE_API_TOKEN`: token com permissão **Workers AI: Read**.
- `CLOUDFLARE_AI_MODEL`: `@cf/cloudflare/clef` ou `@cf/cloudflare/clef-flash`.
- `GOOGLE_MAPS_API_KEY`: chave com a Google Routes API habilitada; necessária
  apenas para os fluxos de trânsito.

Ou informe o ticket interativamente:

```bash
python3 app.py
```

Exemplo de saída JSON para integrar com outro serviço:

```bash
python3 app.py --msg "A API está fora do ar para todos" --channel chat --tier enterprise --json
```

Para usar a variante mais rápida, defina `CLOUDFLARE_AI_MODEL=@cf/cloudflare/clef-flash`.
O código aceita `@cf/cloudflare/clef` e `@cf/cloudflare/clef-flash`.

## Análise de trânsito

Configure `GOOGLE_MAPS_API_KEY` no `.env` e execute:

```bash
python3 app.py --traffic --json
```

O comando consulta a Google Routes API entre Brasília e Valparaíso de Goiás com
`TRAFFIC_AWARE_OPTIMAL`, identifica intervalos `SLOW` e `TRAFFIC_JAM`, calcula as
coordenadas centrais de cada trecho e gera links para abrir os pontos no Google
Maps. Em seguida, o Clef escolhe o ponto mais crítico, estima a criticidade e
recomenda `monitorar`, `alertar`, `despachar` ou `redirecionar`.

Para outra rota:

```bash
python3 app.py --traffic \
  --origin "Brasília, DF" \
  --destination "Valparaíso de Goiás, GO" \
  --json
```

As polilinhas com trânsito têm custo maior na Google Routes API. A aplicação
retorna coordenadas e URLs do Maps, mas não executa redirecionamento de
motoristas nem despacha equipes automaticamente.

## Painel visual

Para abrir o mapa interativo local:

```bash
python3 web.py
```

Acesse `http://127.0.0.1:8000`. O painel mantém as credenciais no servidor,
desenha a rota e os trechos críticos no mapa, e mostra a recomendação do Clef.

Para encerrar o painel, pressione `Ctrl+C`. O servidor escuta somente em
`127.0.0.1`.

## Testes

Execute os testes unitários e a verificação de sintaxe:

```bash
python3 -m unittest -v
python3 -m py_compile app.py traffic.py web.py test_app.py test_traffic.py
```

O teste de trânsito usa dados simulados. A consulta real depende de uma chave
válida da Google Routes API e pode gerar cobrança.

## Fluxo da decisão

1. Envia estado do ticket e perguntas tipadas via REST para `/accounts/{account_id}/ai/run/{model}`.
2. Valida localmente as respostas e as probabilidades retornadas pelo Clef.
3. Política determinística aplica quarentena para spam, revisão manual quando a
   confiança derivada é menor que `0.55`, roteamento de departamento e SLA.

As ações são apenas retornadas como dados: o programa não envia respostas ao
cliente, não altera tickets e não executa operações externas.

O painel usa Leaflet/OpenStreetMap para desenhar o mapa; os dados de rota e
trânsito vêm da Google Routes API, e cada ponto crítico inclui um link para
abertura no Google Maps.
