
#!/bin/bash
set -euo pipefail

echo "🍏 Instalando ai-memory nativo no macOS..."

TARGET_DIR="$HOME/Applications/ai-memory"
BIN_DIR="$HOME/.local/bin"

# Detectar arquitetura
ARCH="$(uname -m)"

case "$ARCH" in
  arm64)
    TARBALL="ai-memory-macos-aarch64.tar.gz"
    echo "🧠 Apple Silicon (ARM64)"
    ;;
  x86_64)
    TARBALL="ai-memory-macos-x86_64.tar.gz"
    echo "🧠 Intel (x86_64)"
    ;;
  *)
    echo "❌ Arquitetura não suportada: $ARCH"
    exit 1
    ;;
esac

# URL corrigida: interpolação correta de TARBALL
BASE_URL="https://github.com/akitaonrails/ai-memory/releases/latest/download"
URL="${BASE_URL}/${TARBALL}"

mkdir -p "$TARGET_DIR" "$BIN_DIR"
cd "$TARGET_DIR"

echo "📥 Baixando: $URL"
curl --fail --location --silent --show-error \
  --retry 3 \
  --output "$TARBALL" \
  "$URL"

echo "📦 Validando arquivo..."
tar -tzf "$TARBALL" >/dev/null

echo "📦 Extraindo pacote..."
tar -xzf "$TARBALL"

rm -f "$TARBALL"

# Confirmar que o binário existe
if [ ! -f "$TARGET_DIR/ai-memory" ]; then
  echo "❌ Binário ai-memory não encontrado."
  echo "Conteúdo extraído:"
  ls -la "$TARGET_DIR"
  exit 1
fi

chmod +x "$TARGET_DIR/ai-memory"

# Criar link simbólico
ln -sfn "$TARGET_DIR/ai-memory" "$BIN_DIR/ai-memory"

# Configurar PATH no Zsh (padrão do macOS)
SHELL_RC="$HOME/.zshrc"
touch "$SHELL_RC"

if ! grep -Fq '$HOME/.local/bin' "$SHELL_RC"; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
fi

echo ""
echo "---------------------------------------------------------"
echo "🎉 INSTALAÇÃO CONCLUÍDA COM SUCESSO!"
echo "---------------------------------------------------------"
echo "💡 Para atualizar o terminal atual e ativar o comando, execute:"
echo "   source $SHELL_RC"
echo ""
echo "▶️  Agora, inicialize o banco de dados rodando:"
echo "   ai-memory init"
echo "---------------------------------------------------------"

