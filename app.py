from flask import Flask, render_template, request, redirect, url_for, jsonify
import os
import re
import json
import base64
from urllib.parse import urlencode
from urllib import request as urllib_request
from datetime import datetime, timedelta
from sqlalchemy import text
from database import db
from models import Profissional, Servico, ProfissionalServico, Agendamento

def create_app():
    app = Flask(__name__)

    # Garante que a pasta 'instance' exista
    instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)

    # CONFIGURAÇÃO DO BANCO
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_path, "agenda.db")}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()

        def ler_env(nome, padrao=""):
            valor = os.environ.get(nome, padrao)
            if valor is None:
                return ""
            texto = str(valor).strip()
            if len(texto) >= 2 and ((texto[0] == '"' and texto[-1] == '"') or (texto[0] == "'" and texto[-1] == "'")):
                texto = texto[1:-1].strip()
            return texto

        def converter_hora_str_para_time(valor_hora):
            return datetime.strptime(valor_hora, "%H:%M").time()

        def calcular_hora_fim(hora_inicio, duracao_minutos):
            base = datetime.combine(datetime.today(), hora_inicio)
            return (base + timedelta(minutes=duracao_minutos)).time()

        def verificar_conflito(profissional_id, data_agendamento, hora_inicio, hora_fim):
            agendamentos = Agendamento.query.filter_by(
                profissional_id=profissional_id,
                data=data_agendamento
            ).all()

            for agendamento in agendamentos:
                if hora_inicio < agendamento.hora_fim and hora_fim > agendamento.hora_inicio:
                    return True

            return False

        def buscar_disponibilidade(servico_id, data_texto):
            disponibilidade = {}

            if not servico_id or not data_texto:
                return disponibilidade

            servico = Servico.query.get(servico_id)
            if not servico:
                return disponibilidade

            data_agendamento = datetime.strptime(data_texto, "%Y-%m-%d").date()

            vinculos = ProfissionalServico.query.filter_by(servico_id=servico.id).all()
            profissional_ids = [v.profissional_id for v in vinculos]
            profissionais = Profissional.query.filter(Profissional.id.in_(profissional_ids)).all() if profissional_ids else []

            for profissional in profissionais:
                inicio_turno = converter_hora_str_para_time(profissional.horario_inicio)
                fim_turno = converter_hora_str_para_time(profissional.horario_fim)

                cursor = datetime.combine(datetime.today(), inicio_turno)
                limite = datetime.combine(datetime.today(), fim_turno)

                while True:
                    inicio_slot = cursor.time()
                    fim_slot = calcular_hora_fim(inicio_slot, servico.duracao)

                    if datetime.combine(datetime.today(), fim_slot) > limite:
                        break

                    if not verificar_conflito(
                        profissional.id,
                        data_agendamento,
                        inicio_slot,
                        fim_slot
                    ):
                        chave_hora = inicio_slot.strftime("%H:%M")
                        if chave_hora not in disponibilidade:
                            disponibilidade[chave_hora] = []

                        disponibilidade[chave_hora].append(
                            {
                                "id": profissional.id,
                                "nome": profissional.nome
                            }
                        )

                    cursor += timedelta(minutes=30)

            return disponibilidade

        def normalizar_telefone(telefone):
            digitos = re.sub(r"\D", "", telefone or "")
            if not digitos:
                return ""

            if digitos.startswith("55"):
                return digitos

            if len(digitos) in (10, 11):
                return f"55{digitos}"

            return digitos

        def telefone_para_twilio(telefone):
            digitos = normalizar_telefone(telefone)
            if not digitos:
                return ""
            return f"+{digitos}"

        def montar_mensagem_confirmacao(agendamento):
            return (
                f"Olá, {agendamento.cliente_nome}! "
                f"Lembrete do seu agendamento no dia {agendamento.data.strftime('%d/%m/%Y')}, "
                f"às {agendamento.hora_inicio.strftime('%H:%M')}. "
                "Responda 1 para confirmar ou 2 para cancelar."
            )

        def enviar_whatsapp_cloud_api(telefone, mensagem, api_token, phone_number_id):
            api_version = ler_env("WHATSAPP_API_VERSION", "v21.0")
            api_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"

            payload = json.dumps({
                "messaging_product": "whatsapp",
                "to": telefone,
                "type": "text",
                "text": {"body": mensagem}
            }).encode("utf-8")

            req = urllib_request.Request(
                api_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}"
                },
                method="POST"
            )

            try:
                with urllib_request.urlopen(req, timeout=20) as response:
                    return 200 <= response.status < 300
            except Exception:
                return False

        def enviar_whatsapp_twilio(telefone, mensagem):
            account_sid = ler_env("TWILIO_ACCOUNT_SID")
            auth_token = ler_env("TWILIO_AUTH_TOKEN")
            numero_origem = ler_env("TWILIO_WHATSAPP_FROM")

            if not account_sid or not auth_token or not numero_origem:
                return False

            destino = telefone_para_twilio(telefone)
            if not destino:
                return False

            api_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            payload = urlencode({
                "To": f"whatsapp:{destino}",
                "From": numero_origem,
                "Body": mensagem
            }).encode("utf-8")

            credenciais = f"{account_sid}:{auth_token}".encode("utf-8")
            auth_header = base64.b64encode(credenciais).decode("utf-8")

            req = urllib_request.Request(
                api_url,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {auth_header}"
                },
                method="POST"
            )

            try:
                with urllib_request.urlopen(req, timeout=20) as response:
                    return 200 <= response.status < 300
            except Exception:
                return False

        def enviar_whatsapp_confirmacao(agendamento):
            modo_simulado = ler_env("WHATSAPP_SIMULADO", "1") == "1"
            provider = ler_env("WHATSAPP_PROVIDER", "meta").lower()
            telefone = normalizar_telefone(agendamento.cliente_telefone)
            mensagem = montar_mensagem_confirmacao(agendamento)

            if not telefone:
                return False

            if modo_simulado:
                print(f"[WHATSAPP SIMULADO] Para: {telefone} | Msg: {mensagem}")
                return True

            if provider == "twilio":
                return enviar_whatsapp_twilio(telefone, mensagem)

            api_url = ler_env("WHATSAPP_API_URL")
            api_token = ler_env("WHATSAPP_API_TOKEN")
            phone_number_id = ler_env("WHATSAPP_PHONE_NUMBER_ID")

            if phone_number_id and api_token:
                return enviar_whatsapp_cloud_api(telefone, mensagem, api_token, phone_number_id)

            if not api_url or not api_token:
                return False

            payload = json.dumps({
                "phone": telefone,
                "message": mensagem
            }).encode("utf-8")

            req = urllib_request.Request(
                api_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}"
                },
                method="POST"
            )

            try:
                with urllib_request.urlopen(req, timeout=20) as response:
                    return 200 <= response.status < 300
            except Exception:
                return False

        def atualizar_status_por_resposta_whatsapp(remetente, texto_resposta):
            resposta = (texto_resposta or "").strip()
            if resposta not in ("1", "2"):
                return False

            telefone = normalizar_telefone(remetente)
            if not telefone:
                return False

            hoje = datetime.today().date()
            candidatos = Agendamento.query.filter(
                Agendamento.data >= hoje
            ).order_by(Agendamento.data.asc(), Agendamento.hora_inicio.asc()).all()

            agendamento_alvo = None
            for agendamento in candidatos:
                if normalizar_telefone(agendamento.cliente_telefone) == telefone:
                    agendamento_alvo = agendamento
                    break

            if not agendamento_alvo:
                return False

            agendamento_alvo.status = "confirmado" if resposta == "1" else "cancelado"
            db.session.commit()
            return True

        def processar_lembretes_whatsapp():
            amanha = datetime.today().date() + timedelta(days=1)

            pendentes = Agendamento.query.filter(
                Agendamento.data == amanha,
                Agendamento.lembrete_whatsapp_ativo == True,
                Agendamento.lembrete_whatsapp_enviado_em.is_(None)
            ).all()

            enviados = 0
            falhas = 0

            for agendamento in pendentes:
                ok = enviar_whatsapp_confirmacao(agendamento)
                if ok:
                    agendamento.lembrete_whatsapp_enviado_em = datetime.utcnow()
                    enviados += 1
                else:
                    falhas += 1

            db.session.commit()
            return enviados, falhas

        def garantir_colunas_agendamento():
            db.session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS agendamento (
                        id INTEGER NOT NULL,
                        cliente_nome VARCHAR(100) NOT NULL DEFAULT '',
                        cliente_telefone VARCHAR(20) NOT NULL DEFAULT '',
                        profissional_id INTEGER,
                        servico_id INTEGER,
                        data DATE NOT NULL,
                        hora_inicio TIME NOT NULL,
                        hora_fim TIME NOT NULL,
                        status VARCHAR(20),
                        criado_em DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(profissional_id) REFERENCES profissional (id),
                        FOREIGN KEY(servico_id) REFERENCES servico (id)
                    )
                    """
                )
            )

            colunas = db.session.execute(text("PRAGMA table_info(agendamento)")).fetchall()
            nomes_colunas = [coluna[1] for coluna in colunas]

            if "cliente_nome" not in nomes_colunas:
                db.session.execute(text("ALTER TABLE agendamento ADD COLUMN cliente_nome VARCHAR(100) NOT NULL DEFAULT ''"))

            if "cliente_telefone" not in nomes_colunas:
                db.session.execute(text("ALTER TABLE agendamento ADD COLUMN cliente_telefone VARCHAR(20) NOT NULL DEFAULT ''"))

            if "lembrete_whatsapp_ativo" not in nomes_colunas:
                db.session.execute(text("ALTER TABLE agendamento ADD COLUMN lembrete_whatsapp_ativo BOOLEAN NOT NULL DEFAULT 0"))

            if "lembrete_whatsapp_enviado_em" not in nomes_colunas:
                db.session.execute(text("ALTER TABLE agendamento ADD COLUMN lembrete_whatsapp_enviado_em DATETIME"))

            db.session.commit()

        garantir_colunas_agendamento()

        # =========================
        # ROTAS
        # =========================

        @app.route("/")
        def index():
            return """
            <h1>Sistema de Agenda</h1>
            <a href='/profissionais'>Profissionais</a><br>
            <a href='/servicos'>Serviços</a><br>
            <a href='/agendamentos'>Agendamentos</a><br>
            <a href='/agenda/manual'>Agenda Manual</a><br>
            <a href='/agenda/manual-preview'>Prévia Agenda Manual</a>
            """

        # =========================
        # PROFISSIONAIS
        # =========================

        @app.route("/profissionais")
        def listar_profissionais():
            profissionais = Profissional.query.all()
            vinculos = ProfissionalServico.query.all()
            servicos = Servico.query.all()

            servicos_por_id = {servico.id: servico.nome for servico in servicos}
            profissionais_servicos = {profissional.id: [] for profissional in profissionais}

            for vinculo in vinculos:
                if vinculo.profissional_id in profissionais_servicos:
                    nome_servico = servicos_por_id.get(vinculo.servico_id)
                    if nome_servico:
                        profissionais_servicos[vinculo.profissional_id].append(nome_servico)

            return render_template(
                "profissionais.html",
                profissionais=profissionais,
                profissionais_servicos=profissionais_servicos
            )

        @app.route("/profissionais/novo", methods=["GET", "POST"])
        def novo_profissional():
            servicos = Servico.query.all()

            if request.method == "POST":
                nome = request.form["nome"]
                horario_inicio = request.form["horario_inicio"]
                horario_fim = request.form["horario_fim"]
                servico_ids = request.form.getlist("servico_ids")

                novo = Profissional(
                    nome=nome,
                    horario_inicio=horario_inicio,
                    horario_fim=horario_fim
                )
                db.session.add(novo)
                db.session.commit()

                for servico_id in servico_ids:
                    db.session.add(
                        ProfissionalServico(
                            profissional_id=novo.id,
                            servico_id=servico_id
                        )
                    )
                db.session.commit()

                return redirect(url_for("listar_profissionais"))
            return render_template("novo_profissional.html", servicos=servicos)

        @app.route("/profissionais/<int:profissional_id>/editar", methods=["GET", "POST"])
        def editar_profissional(profissional_id):
            profissional = Profissional.query.get_or_404(profissional_id)
            servicos = Servico.query.all()
            vinculos_atuais = ProfissionalServico.query.filter_by(profissional_id=profissional_id).all()
            servicos_selecionados = [vinculo.servico_id for vinculo in vinculos_atuais]

            if request.method == "POST":
                profissional.nome = request.form["nome"]
                profissional.horario_inicio = request.form["horario_inicio"]
                profissional.horario_fim = request.form["horario_fim"]

                novos_servicos_ids = request.form.getlist("servico_ids")
                ProfissionalServico.query.filter_by(profissional_id=profissional_id).delete()
                for servico_id in novos_servicos_ids:
                    db.session.add(
                        ProfissionalServico(
                            profissional_id=profissional_id,
                            servico_id=servico_id
                        )
                    )

                db.session.commit()
                return redirect(url_for("listar_profissionais"))

            return render_template(
                "editar_profissional.html",
                profissional=profissional,
                servicos=servicos,
                servicos_selecionados=servicos_selecionados
            )

        @app.route("/profissionais/<int:profissional_id>/excluir", methods=["POST"])
        def excluir_profissional(profissional_id):
            profissional = Profissional.query.get_or_404(profissional_id)
            ProfissionalServico.query.filter_by(profissional_id=profissional_id).delete()
            db.session.delete(profissional)
            db.session.commit()
            return redirect(url_for("listar_profissionais"))

        # =========================
        # SERVIÇOS
        # =========================

        @app.route("/servicos")
        def listar_servicos():
            servicos = Servico.query.all()
            return render_template("servicos.html", servicos=servicos)

        @app.route("/servicos/novo", methods=["GET", "POST"])
        def novo_servico():
            if request.method == "POST":
                nome = request.form["nome"]
                duracao = request.form["duracao"]
                novo = Servico(
                    nome=nome,
                    duracao=duracao
                )
                db.session.add(novo)
                db.session.commit()
                return redirect(url_for("listar_servicos"))
            return render_template("novo_servico.html")

        # =========================
        # AGENDAMENTOS
        # =========================

        @app.route("/agendamentos")
        def listar_agendamentos():
            agendamentos = Agendamento.query.order_by(Agendamento.data.asc(), Agendamento.hora_inicio.asc()).all()
            profissionais = {p.id: p.nome for p in Profissional.query.all()}
            servicos = {s.id: s.nome for s in Servico.query.all()}

            enviados = request.args.get("enviados")
            falhas = request.args.get("falhas")

            return render_template(
                "agendamentos.html",
                agendamentos=agendamentos,
                profissionais=profissionais,
                servicos=servicos,
                enviados=enviados,
                falhas=falhas
            )

        @app.route("/agendamentos/<int:agendamento_id>/lembrete-whatsapp/ativar", methods=["POST"])
        def ativar_lembrete_whatsapp(agendamento_id):
            agendamento = Agendamento.query.get_or_404(agendamento_id)
            agendamento.lembrete_whatsapp_ativo = True
            agendamento.lembrete_whatsapp_enviado_em = None
            db.session.commit()
            return redirect(url_for("listar_agendamentos"))

        @app.route("/agendamentos/<int:agendamento_id>/lembrete-whatsapp/desativar", methods=["POST"])
        def desativar_lembrete_whatsapp(agendamento_id):
            agendamento = Agendamento.query.get_or_404(agendamento_id)
            agendamento.lembrete_whatsapp_ativo = False
            agendamento.lembrete_whatsapp_enviado_em = None
            db.session.commit()
            return redirect(url_for("listar_agendamentos"))

        @app.route("/notificacoes/whatsapp/processar", methods=["POST"])
        def processar_notificacoes_whatsapp():
            enviados, falhas = processar_lembretes_whatsapp()
            return redirect(url_for("listar_agendamentos", enviados=enviados, falhas=falhas))

        @app.route("/agendamentos/<int:agendamento_id>/lembrete-whatsapp/enviar", methods=["POST"])
        def enviar_lembrete_whatsapp_agendamento(agendamento_id):
            agendamento = Agendamento.query.get_or_404(agendamento_id)
            ok = enviar_whatsapp_confirmacao(agendamento)

            if ok:
                agendamento.lembrete_whatsapp_ativo = True
                agendamento.lembrete_whatsapp_enviado_em = datetime.utcnow()
                db.session.commit()
                return redirect(url_for("listar_agendamentos", enviados=1, falhas=0))

            return redirect(url_for("listar_agendamentos", enviados=0, falhas=1))

        @app.route("/webhooks/whatsapp", methods=["GET", "POST"])
        def webhook_whatsapp():
            provider = ler_env("WHATSAPP_PROVIDER", "meta").lower()

            if request.method == "GET":
                mode = request.args.get("hub.mode")
                verify_token = request.args.get("hub.verify_token")
                challenge = request.args.get("hub.challenge", "")
                esperado = ler_env("WHATSAPP_WEBHOOK_VERIFY_TOKEN")

                if mode == "subscribe" and esperado and verify_token == esperado:
                    return challenge, 200
                return "Token inválido", 403

            if provider == "twilio":
                remetente = request.form.get("From", "")
                texto = request.form.get("Body", "")

                if remetente.lower().startswith("whatsapp:"):
                    remetente = remetente.split(":", 1)[1]

                atualizar_status_por_resposta_whatsapp(remetente, texto)
                return "", 200

            payload = request.get_json(silent=True) or {}

            entries = payload.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    messages = value.get("messages", [])

                    for mensagem in messages:
                        if mensagem.get("type") != "text":
                            continue

                        remetente = mensagem.get("from", "")
                        texto = mensagem.get("text", {}).get("body", "")
                        atualizar_status_por_resposta_whatsapp(remetente, texto)

            return jsonify({"status": "ok"}), 200

        @app.route("/agendamentos/novo", methods=["GET", "POST"])
        def novo_agendamento():
            servicos = Servico.query.all()
            profissionais = Profissional.query.all()

            servico_id_query = request.args.get("servico_id", type=int)
            data_query = request.args.get("data", default="")
            disponibilidade = buscar_disponibilidade(servico_id_query, data_query) if servico_id_query and data_query else {}

            erro = None

            if request.method == "POST":
                cliente_nome = request.form["cliente_nome"].strip()
                cliente_telefone = request.form["cliente_telefone"].strip()
                profissional_id = int(request.form["profissional_id"])
                servico_id = int(request.form["servico_id"])
                data_texto = request.form["data"]
                hora_inicio_texto = request.form["hora_inicio"]

                servico = Servico.query.get_or_404(servico_id)
                data_agendamento = datetime.strptime(data_texto, "%Y-%m-%d").date()
                hora_inicio = converter_hora_str_para_time(hora_inicio_texto)
                hora_fim = calcular_hora_fim(hora_inicio, servico.duracao)

                vinculo = ProfissionalServico.query.filter_by(
                    profissional_id=profissional_id,
                    servico_id=servico_id
                ).first()

                if not vinculo:
                    erro = "Esse profissional não está vinculado ao serviço selecionado."
                elif verificar_conflito(profissional_id, data_agendamento, hora_inicio, hora_fim):
                    erro = "Conflito de horário: já existe agendamento nesse intervalo."
                else:
                    novo = Agendamento(
                        cliente_nome=cliente_nome,
                        cliente_telefone=cliente_telefone,
                        profissional_id=profissional_id,
                        servico_id=servico_id,
                        data=data_agendamento,
                        hora_inicio=hora_inicio,
                        hora_fim=hora_fim,
                        status="agendado"
                    )
                    db.session.add(novo)
                    db.session.commit()
                    return redirect(url_for("listar_agendamentos"))

                disponibilidade = buscar_disponibilidade(servico_id, data_texto)

            return render_template(
                "novo_agendamento.html",
                servicos=servicos,
                profissionais=profissionais,
                disponibilidade=disponibilidade,
                servico_id_query=servico_id_query,
                data_query=data_query,
                erro=erro
            )

        @app.route("/agenda/manual", methods=["GET", "POST"])
        def agenda_manual():
            data_texto = request.args.get("data") or request.form.get("data") or datetime.today().strftime("%Y-%m-%d")
            data_agendamento = datetime.strptime(data_texto, "%Y-%m-%d").date()

            profissionais_reais = Profissional.query.order_by(Profissional.nome.asc()).limit(5).all()
            profissionais = [{"id": p.id, "nome": p.nome} for p in profissionais_reais]

            while len(profissionais) < 5:
                profissionais.append({"id": None, "nome": "(sem profissional)"})

            if request.method == "POST":
                profissional_id = request.form.get("profissional_id", type=int)
                hora_inicio_texto = request.form["hora_inicio"]
                cliente_nome = request.form.get("cliente_nome", "").strip()
                cliente_telefone = request.form.get("cliente_telefone", "").strip()

                if profissional_id:
                    hora_inicio = converter_hora_str_para_time(hora_inicio_texto)
                    hora_fim = calcular_hora_fim(hora_inicio, 30)

                    existente = Agendamento.query.filter_by(
                        data=data_agendamento,
                        profissional_id=profissional_id,
                        hora_inicio=hora_inicio
                    ).first()

                    if cliente_nome:
                        if existente:
                            existente.cliente_nome = cliente_nome
                            existente.cliente_telefone = cliente_telefone
                            existente.hora_fim = hora_fim
                            existente.status = "agendado"
                        else:
                            novo = Agendamento(
                                cliente_nome=cliente_nome,
                                cliente_telefone=cliente_telefone,
                                profissional_id=profissional_id,
                                servico_id=None,
                                data=data_agendamento,
                                hora_inicio=hora_inicio,
                                hora_fim=hora_fim,
                                status="agendado"
                            )
                            db.session.add(novo)
                    elif existente:
                        db.session.delete(existente)

                    db.session.commit()

                return redirect(url_for("agenda_manual", data=data_texto))

            horarios = []
            cursor = datetime.strptime("08:00", "%H:%M")
            fim = datetime.strptime("18:30", "%H:%M")
            while cursor <= fim:
                horarios.append(cursor.strftime("%H:%M"))
                cursor += timedelta(minutes=30)

            ids_profissionais = [p["id"] for p in profissionais if p["id"] is not None]
            agendamentos = (
                Agendamento.query.filter(
                    Agendamento.data == data_agendamento,
                    Agendamento.profissional_id.in_(ids_profissionais)
                ).all()
                if ids_profissionais else []
            )

            agenda_mapa = {}
            for agendamento in agendamentos:
                chave = (agendamento.hora_inicio.strftime("%H:%M"), agendamento.profissional_id)
                agenda_mapa[chave] = {
                    "cliente_nome": agendamento.cliente_nome,
                    "cliente_telefone": agendamento.cliente_telefone or ""
                }

            return render_template(
                "agenda_manual.html",
                dia=data_agendamento.strftime("%d/%m/%Y"),
                data_iso=data_texto,
                horarios=horarios,
                profissionais=profissionais,
                agenda_mapa=agenda_mapa
            )

        @app.route("/agenda/manual-preview")
        def agenda_manual_preview():
            data_texto = request.args.get("data") or datetime.today().strftime("%Y-%m-%d")
            try:
                data_agendamento = datetime.strptime(data_texto, "%Y-%m-%d").date()
            except ValueError:
                data_agendamento = datetime.today().date()
                data_texto = data_agendamento.strftime("%Y-%m-%d")

            profissionais_reais = Profissional.query.order_by(Profissional.nome.asc()).limit(5).all()
            profissionais = [{"id": p.id, "nome": p.nome} for p in profissionais_reais]

            while len(profissionais) < 5:
                profissionais.append({"id": None, "nome": "(sem profissional)"})

            horarios = []
            cursor = datetime.strptime("08:00", "%H:%M")
            fim = datetime.strptime("18:30", "%H:%M")
            while cursor <= fim:
                horarios.append(cursor.strftime("%H:%M"))
                cursor += timedelta(minutes=30)

            agenda_mapa = {horario: ["", "", "", "", ""] for horario in horarios}
            indice_profissional = {
                p["id"]: indice for indice, p in enumerate(profissionais) if p["id"] is not None
            }

            ids_profissionais = [p["id"] for p in profissionais if p["id"] is not None]
            agendamentos = (
                Agendamento.query.filter(
                    Agendamento.data == data_agendamento,
                    Agendamento.profissional_id.in_(ids_profissionais)
                ).all()
                if ids_profissionais else []
            )

            for agendamento in agendamentos:
                horario = agendamento.hora_inicio.strftime("%H:%M")
                indice = indice_profissional.get(agendamento.profissional_id)
                if indice is None or horario not in agenda_mapa:
                    continue

                agenda_mapa[horario][indice] = agendamento.cliente_nome

            return render_template(
                "agenda_manual_preview.html",
                dia=data_agendamento.strftime("%d/%m/%Y"),
                data_iso=data_texto,
                horarios=horarios,
                profissionais=profissionais,
                agenda_mapa=agenda_mapa
            )

    return app

# =========================
# INICIALIZAÇÃO
# =========================

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)