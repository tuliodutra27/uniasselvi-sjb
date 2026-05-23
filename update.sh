#!/bin/bash
set -e

echo "==> Baixando atualizações do GitHub..."
git pull

echo "==> Reconstruindo e reiniciando o container..."
docker compose up -d --build

echo ""
echo "==> App atualizado com sucesso!"
docker ps | grep uniasselvi-sjb
