# Maintainer: iamusernet <iamusernet@users.noreply.github.com>

pkgname=ghostprovider
pkgver=1.0.0
pkgrel=1
pkgdesc="TUI for self-hosting & localhost management"
arch=('any')
url="https://github.com/iamnetuseroagent/ghostprovider"
license=('MIT')
depends=('python' 'docker' 'git' 'python-pip')
makedepends=('git')
source=("$pkgname::git+https://github.com/iamnetuseroagent/ghostprovider.git")
sha256sums=('SKIP')

package() {
  cd "$srcdir/$pkgname"

  install -Dm755 ghostprovider.sh "$pkgdir/usr/bin/ghostprovider"
  install -d "$pkgdir/opt/$pkgname"

  cp -r ghostprovider "$pkgdir/opt/$pkgname/"
  cp pyproject.toml "$pkgdir/opt/$pkgname/"

  python -m venv "$pkgdir/opt/$pkgname/.venv"
  . "$pkgdir/opt/$pkgname/.venv/bin/activate"
  pip install --no-cache-dir -e "$pkgdir/opt/$pkgname"
}
