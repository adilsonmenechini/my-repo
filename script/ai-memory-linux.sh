#!/usr/bin/env bash
set -Eeuo pipefail

echo "🐧 Instalando ai-memory no Ubuntu..."

# Verificar dependências
for cmd in curl tar uname; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ Dependência ausente: $cmd"
    echo "Instale com: sudo apt update && sudo apt install -y curl tar"
    exit 1
  fi
done

# Detectar arquitetura
case "$(uname -m)" in
  x86_64)
    TARBALL="ai-memory-linux-x86_64.tar.gz"
    echo "🧠 Arquitetura detectada: Linux x86_64"
    ;;
  aarch64|arm64)
    TARBALL="ai-memory-linux-aarch64.tar.gz"
    echo "🧠 Arquitetura detectada: Linux ARM64"
    ;;
  *)
    echo "❌ Arquitetura não suportada: $(uname -m)"
    exit 1
    ;;
esac

TARGET_DIR="$HOME/Applications/ai-memory"
BIN_DIR="$HOME/.local/bin"
BASE_URL="https://github.com/akitaonrails/ai-memory/releases/latest/download"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$TARGET_DIR" "$BIN_DIR"

echo "📥 Baixando ${TARBALL}..."
curl -fLsS --retry 3 \
  "${BASE_URL}/${TARBALL}" \
  -o "${TMP_DIR}/${TARBALL}"

echo "📦 Validando pacote..."
tar -tzf "${TMP_DIR}/${TARBALL}" >/dev/null

echo "📦 Extraindo arquivos..."
tar -xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"

# Localizar o executável dentro do pacote
BINARY="$(find "$TMP_DIR" -type f -name ai-memory \
  ! -path "$TMP_DIR/ai-memory" -print -quit)"

if [ -z "$BINARY" ]; then
  # Caso o binário esteja na raiz da extração
  if [ -f "$TMP_DIR/ai-memory" ]; then
    BINARY="$TMP_DIR/ai-memory"
  else
    echo "❌ Binário ai-memory não encontrado no pacote."
    echo "Arquivos extraídos:"
    find "$TMP_DIR" -maxdepth 3 -type f -print
    exit 1
  fi
fi

install -m 755 "$BINARY" "${TARGET_DIR}/ai-memory"
ln -sfn "${TARGET_DIR}/ai-memory" "${BIN_DIR}/ai-memory"

# Configurar PATH no Bash e Zsh
for SHELL_RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [ -f "$SHELL_RC" ] &&
     ! grep -Fq '.local/bin' "$SHELL_RC"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
  fi
done

export PATH="$BIN_DIR:$PATH"

echo ""
echo "---------------------------------------------------------"
echo "🎉 CLIENTE HOST PRONTO!"
echo "---------------------------------------------------------"
echo "💡 Para que seu terminal reconheça o comando agora, rode:"
echo "   source $SHELL_RC"
echo ""
echo "▶️  Assim que subir o seu Docker Compose, vincule o OpenCode com:"
echo "   ai-memory install-hooks --agent opencode --apply"
echo "   ai-memory install-mcp --client opencode --apply"
echo "---------------------------------------------------------"
