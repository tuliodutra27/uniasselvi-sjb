# Student Enrollment Tracker

Sistema web para identificar automaticamente novos alunos matriculados em uma faculdade EAD, a partir de arquivos CSV exportados da plataforma.

## Como funciona

1. Exporte o relatório de alunos da plataforma da faculdade em formato `.csv`
2. Faça o upload no sistema
3. O sistema compara os CPFs com todos os uploads anteriores e destaca apenas os alunos novos

O primeiro CSV enviado vira a base histórica. Cada envio seguinte é comparado com o acumulado.

## Requisitos

- Python 3.10+
- pip

## Instalação

```bash
# Clone o repositório
git clone https://github.com/SEU_USUARIO/student-enrollment-tracker.git
cd student-enrollment-tracker

# Crie um ambiente virtual (recomendado)
python -m venv venv
venv\Scripts\activate  # Windows
# ou: source venv/bin/activate  # Linux/Mac

# Instale as dependências
pip install -r requirements.txt
```

## Executando

```bash
python app.py
```

Acesse `http://localhost:5000` no navegador.

## Formato do CSV esperado

O sistema detecta automaticamente as colunas pelo nome. As colunas reconhecidas são:

| Campo       | Nomes aceitos                                      |
|-------------|-----------------------------------------------------|
| CPF         | cpf, documento, doc                                |
| Nome        | nome, name, aluno, nome_aluno                      |
| Matrícula   | id, matricula, codigo, ra, registro                |
| Curso/Turma | curso, course, turma, disciplina, polo             |
| Data        | data, dt_matricula, data_matricula, data_inicio    |

As colunas **CPF** e **Nome** são obrigatórias. As demais são opcionais.
