# Uniasselvi – Polo São João da Barra

Sistema web interno para gestão de matrículas, leads e mensalidades do polo EAD de São João da Barra.

## Funcionalidades

### Leads (inscrições)
- Upload de CSV exportado da plataforma com lista de inscritos
- Identificação automática de alunos **novos** a cada upload (comparação por CPF)
- Extração de telefone e celular direto do CSV
- Armazenamento do CSV bruto para re-processamento futuro sem reenvio
- Filtros por nome, curso, polo, turno, tipo de inscrição, data, contato e lote de upload
- Histórico de todos os uploads com totais e quantidade de novos

### Alunos matriculados
- Upload de CSV com alunos ativos na plataforma
- Visão consolidada com filtros por nome, curso, polo, turno, semestre e situação
- Detalhes por aluno com histórico de anotações (com autor e data/hora)

### Mensalidades
- Upload de CSV com situação de pagamento por aluno
- Dashboard com contagem de adimplentes e inadimplentes
- Filtros por nome, status, curso, polo, turno, semestre, situação e período de pagamento

### Administração
- Autenticação com controle de papéis (admin / usuário)
- Log de auditoria de todas as ações sensíveis
- Gerenciamento de usuários (adicionar, remover, alterar senha e papel)
- Re-processamento de contatos de uploads anteriores (admin)

## Tecnologias

- **Python 3.10+** · Flask 3.1 · SQLite
- **Bootstrap 5.3** com identidade visual Uniasselvi (amarelo #FFB81C / preto #1A1A1A)
- pandas para parsing de CSV · suporte a múltiplas codificações e separadores

## Requisitos

- Python 3.10+
- pip

## Instalação

```bash
git clone https://github.com/tuliodutra27/uniasselvi-sjb.git
cd uniasselvi-sjb

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

## Executando

```bash
python app.py
```

Acesse `http://localhost:5000`. No primeiro acesso, o sistema redireciona para `/setup` para criar o usuário administrador.

## Docker

```bash
docker compose up -d
```

A aplicação sobe na porta `5000`. O banco de dados e os uploads são persistidos em volumes.

## Formato dos CSVs

O sistema detecta colunas automaticamente pelo nome (sem distinção de maiúsculas/minúsculas ou acentos).

### CSV de leads / inscrições

| Campo      | Nomes aceitos                                            |
|------------|----------------------------------------------------------|
| CPF        | cpf, documento, doc                                      |
| Nome       | nome, name, aluno, nome_aluno                            |
| Matrícula  | id, matricula, codigo, ra, registro                      |
| Curso      | curso, course, turma, disciplina                         |
| Polo       | polo, unidade, campus                                    |
| Turno      | turno, periodo, turno_curso                              |
| Tipo       | tipo, tipo_inscricao, modalidade                         |
| Data insc. | data, dt_inscricao, data_inscricao, data_inicio          |
| Telefone   | telefone, fone, tel, phone, telefone_fixo                |
| Celular    | celular, cell, cel, mobile, telefone_celular             |

CPF e Nome são obrigatórios. Os demais campos são opcionais.

### CSV de alunos matriculados e mensalidades

Segue o mesmo padrão de detecção flexível. Campos adicionais como semestre, situação do aluno, último pagamento e quantidade paga são mapeados automaticamente quando presentes.
