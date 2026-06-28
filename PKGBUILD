pkgname=ghostprovider
pkgver=1.0.0
pkgrel=1
pkgdesc="TUI for self-hosting & localhost management"
arch=('any')
url="https://github.com/iamnetuseragent/ghostprovider"
license=('MIT')
depends=('python' 'docker' 'git' 'python-pip')
makedepends=('git')
source=("$pkgname::git+https://github.com/iamnetuseragent/ghostprovider.git")
sha256sums=('SKIP')

package() {
  cd "$srcdir/$pkgname"

  install -dm755 "$pkgdir/usr/bin"
  cat > "$pkgdir/usr/bin/ghostprovider" << 'EOF'
#!/bin/bash
exec /opt/ghostprovider/.venv/bin/python3 -m ghostprovider "$@"
EOF
  chmod 755 "$pkgdir/usr/bin/ghostprovider"

  install -d "$pkgdir/opt/$pkgname"

  cp -r ghostprovider "$pkgdir/opt/$pkgname/"
  cp pyproject.toml "$pkgdir/opt/$pkgname/"

  python -m venv "$pkgdir/opt/$pkgname/.venv"
  "$pkgdir/opt/$pkgname/.venv/bin/pip" install --no-cache-dir "$pkgdir/opt/$pkgname"
}
