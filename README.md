# Relatório de Jornada do Motorista (PDF) — API Cobli

Gera, a partir da API pública da Cobli, um PDF no layout do relatório de jornada que o cliente já usa:
cabeçalho (cliente/período/motorista), totais do período e, para cada jornada, o resumo
(trabalho, direção, parada, descanso intra/interjornada, hora extra, noturno) e a linha do tempo de eventos.

## Como abrir
1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e coloque a chave de API e o nome do cliente
   (esse arquivo não vai para o GitHub).
2. Dois cliques em **ABRIR_SISTEMA.bat**, ou no PowerShell:
   ```powershell
   py -m pip install -r requirements.txt
   py -m streamlit run app.py
   ```

## Abas
1. **Relatório de jornada** — escolhe motorista (ou todos, em .zip) e período → PDF.
2. **Calendário de rotas** — escolhe o motorista e as datas; o calendário pinta cada dia: verde = rota planejada,
   azul = jornada marcada no app, amarelo = só rodou, cinza = sem rota. Em "Ver detalhes do dia" escolha a data para ver os checklists.

## De onde vêm os dados
| Fonte | Endpoint | Uso |
|---|---|---|
| Checklists do app | `GET /checklists/completed-checklists` | **Fonte principal**: Início, Espera, Descarga, Refeição, Direção, Descanso interjornada, Término |
| Rastreador | `GET /public/v1/paths` | km, motor ocioso, coordenadas e correções quando o motorista esquece de marcar |
| Rotas | `GET /public/v2/routes` | consulta de rota por data |
| Cadastros | `GET /public/v1/drivers`, `/public/v1/vehicles` | nomes e placas |

## Regras
- O relatório mostra **somente os checklists que o motorista marcou** no app; cada evento dura até o próximo.
- "Descanso interjornada" encerra a jornada e o "fim do descanso" abre a próxima.
- Se faltar Início ou Término, a linha aparece como "não marcado no app" e o horário com *.
- Motorista sem checklist no período não gera relatório.
- Trabalho = duração − refeição − descanso. Hora extra = trabalho − 8 h. Km vem do rastreador.

