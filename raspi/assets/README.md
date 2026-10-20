#README.md  ver 3

# POC Raspberry Pi display assets

The `splash` and `wallpaper` directories contain Middlesex-branded PNG files
for the supported test and target panels.

## Filenames and sizes

- `*_800x480.png`: original Raspberry Pi 7-inch touch display
- `*_1280x720.png`: Raspberry Pi Touch Display 2
- `*_1280x800.png`: Waveshare 10.1-inch DSI display

The Raspberry Pi Touch Display 2 boot theme additionally uses:

- `splash/poc_splash_touch2_boot.png`: 720 x 1280, made by rotating the
  complete 1280 x 720 landscape artwork 90 degrees counter-clockwise.

Keep these filenames when replacing the artwork. Use non-interlaced,
8-bit RGB or RGBA PNG files. Keep important content at least 40 pixels from the
edge.

Install the Touch Display 2 splash and wallpaper from the live runtime with:

```bash
sudo ./raspi/install-display-assets.sh --user poc
```

The script validates the Touch 2 splash and wallpaper dimensions, installs the
`poc` Plymouth theme, forces Plymouth `DeviceScale=1`, rebuilds the initramfs
and applies the wallpaper. The splash is drawn at `(0,0)` without scaling on
the native 720 x 1280 boot canvas. Desktop and touch rotation are separate and
are handled by the `kanshi` profile installed by `setup-console.sh`.
