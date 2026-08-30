# Maintainer: Zedwil <nylegarcia01@gmail.com>
# Contributor: rikkichy
pkgname=openwave
pkgver=1.1.0
pkgrel=1
pkgdesc="The audio mixing matrix for Linux — per-app mixes, per-mix outputs, Elgato Wave control"
arch=('any')
url="https://github.com/NyleGarcia/openwave"
license=('MIT')
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'libusb' 'pipewire')
optdepends=('python-xlib: friendly app names in the Add Source picker')
source=("https://github.com/NyleGarcia/openwave/releases/download/v$pkgver/$pkgname-$pkgver.tar.gz")
sha256sums=('9918ba4c70d685f6f2663390d07fe50f4f98cbb0104e502a91be0bc745322c6c')

check() {
    cd "$srcdir/$pkgname-$pkgver"
    python -m unittest discover -s tests -t .
}

package() {
    cd "$srcdir/$pkgname-$pkgver"
    # The Makefile is the one description of the install layout. This file
    # used to repeat it by hand and drifted -- it was missing the icons and
    # the daemon launcher by the time anyone looked.
    make DESTDIR="$pkgdir" PREFIX=/usr install
}
