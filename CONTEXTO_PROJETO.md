# CONTEXTO DO PROJETO - Agenda WhatsApp

Atualizado em: 26/02/2026

## Visão geral
Sistema em Flask + SQLite para gestão de agenda de estúdio de beleza, com foco em operação manual da agenda e estrutura para notificações por WhatsApp.

## O que já está funcionando

### 1) Cadastros base
- Cadastro e listagem de profissionais.
- Cadastro e listagem de serviços.
- Edição e exclusão de profissionais.
- Vínculo de serviços ao profissional dentro do próprio cadastro/edição do profissional.
- Exibição dos serviços vinculados na tela de profissionais.

### 2) Agenda manual (principal)
- Tela: `/agenda/manual`
- Grade com:
  - Coluna 1: horários de 08:00 até 18:30 (intervalo de 30 min).
  - Colunas 2 a 6: até 5 profissionais.
- Edição por célula:
  - Campos: nome da cliente e telefone.
  - Botões: Salvar e Limpar.
- Persistência no banco por data + profissional + horário.
- Se houver menos de 5 profissionais cadastrados, mostra colunas de placeholder “(sem profissional)”.

### 3) Agendamentos (estrutura adicional)
- Tela de listagem: `/agendamentos`
- Tela de novo agendamento: `/agendamentos/novo`
- Lógica de conflito de horário implementada.
- Lógica de disponibilidade por serviço e data implementada.

### 4) WhatsApp (MVP)
- Botão por agendamento para ativar/desativar lembrete WhatsApp.
- Processador de lembretes para agendamentos de “amanhã”.
- Registro de envio em coluna específica (`lembrete_whatsapp_enviado_em`).
- Modo simulado por padrão (não envia de fato sem API).

## Pontos importantes sobre WhatsApp
- Sem API oficial, envio automático confiável não é recomendado/estável.
- Hoje o sistema está preparado para:
  - Simulação local (logs), e
  - Integração real via API (por variáveis de ambiente).

Variáveis de ambiente usadas no código:
- `WHATSAPP_SIMULADO` (padrão: `1`)
- `WHATSAPP_API_URL`
- `WHATSAPP_API_TOKEN`

## Arquivos principais alterados
- `app.py`
- `models.py`
- `templates/profissionais.html`
- `templates/novo_profissional.html`
- `templates/editar_profissional.html`
- `templates/servicos.html`
- `templates/agendamentos.html`
- `templates/novo_agendamento.html`
- `templates/agenda_manual.html`
- `templates/agenda_manual_preview.html`

## Próximos passos sugeridos (ordem)
1. Ajustar UX da agenda manual (ex.: destaque visual de célula ocupada/livre).
2. Definir fluxo oficial de confirmação por WhatsApp:
   - API oficial (recomendado), ou
   - botão “Abrir WhatsApp” semi-automático com mensagem pronta.
3. Agendar execução automática diária do processamento de lembretes (Task Scheduler).
4. Revisar e limpar rotas antigas se necessário.

## Como retomar rapidamente quando voltar
Ao abrir o chat novamente, diga:

"Leia o arquivo CONTEXTO_PROJETO.md e continue a partir da agenda manual e confirmação via WhatsApp."
