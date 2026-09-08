# Maintainer: Zedwil <nylegarcia01@gmail.com>
# Contributor: rikkichy
pkgname=openwave
pkgver=1.2.1
pkgrel=1
pkgdesc="The audio mixing matrix for Linux — per-app mixes, per-mix outputs, Elgato Wave control"
arch=('any')
url="https://github.com/NyleGarcia/openwave"
license=('MIT')
# libadwaita>=1.5 is not cosmetic: the app builds Adw.Dialog/Adw.AlertDialog,
# which 1.4 does not have. polkit supplies the pkexec that first-run setup
# shells to, libpulse the pactl the mixer reads and writes masters with,
# alsa-utils the amixer/aplay device.py discovers ALSA controls through, and
# wireplumber the wpctl behind default-sink lookups.
depends=('python' 'python-gobject' 'gtk4' 'libadwaita>=1.5' 'adwaita-icon-theme'
         'libusb' 'pipewire' 'wireplumber' 'libpulse' 'alsa-utils' 'polkit')
optdepends=('python-xlib: friendly app names in the Add Source picker'
            'swh-plugins: gate and compressor in the microphone DSP chain')
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
