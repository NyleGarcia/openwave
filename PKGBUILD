# Maintainer: Zedwil <nylegarcia01@gmail.com>
# Contributor: rikkichy
pkgname=openwave
pkgver=1.2.1
pkgrel=1
pkgdesc="The audio mixing matrix for Linux — per-app mixes, per-mix outputs, Elgato Wave control"
arch=('any')
url="https://github.com/NyleGarcia/openwave"
license=('MIT')
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'libusb' 'pipewire')
optdepends=('python-xlib: friendly app names in the Add Source picker')
source=("https://github.com/NyleGarcia/openwave/releases/download/v$pkgver/$pkgname-$pkgver.tar.gz")
sha256sums=('eb4420a9dd9291ff0f794639e1ea46fb30f4140221e1aa7654a5dd7a471b023c')

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
