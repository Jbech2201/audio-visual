# Audio Vibe — Spotify & Lirik

Visualisasi **6 mode suasana** yang berjalan berdampingan dengan Spotify: lirik
mengikuti lagu yang sedang diputar, lengkap dengan tab bahasa dan koreksi
offset. Tidak ada upload file audio.

Tombol pemilih mode hanya menampilkan emoji, dengan tooltip dan label aksesibilitas.

- **🔥 Api** — gunung berapi dengan semburan magma berulang, asap dan aliran
  lava bercahaya, equalizer magma, lirik tebal dengan gerakan sangat halus.
- **❄️ Salju** — malam biru beku, salju turun perlahan, equalizer
  kristal es, lirik serif yang tenang.
- **🌸 Sakura** — musim semi romantis bernuansa pink, kelopak
  sakura berlekuk yang jatuh sambil berputar, lirik lembut dan equalizer pink.
- **🍁 Senja** — langit jingga, matahari orange dekat cakrawala,
  sinar matahari hangat, awan dan partikel keemasan.
- **☀️ Siang** — langit biru cerah, matahari kuning dengan pancaran
  cahaya, awan putih bergerak dan kilauan kuning.
- **🌙 Malam** — langit biru gelap dengan bintang berkelip, bulan putih
  bercahaya dan siluet gedung kota berlapis dengan jendela menyala.

## Cara menjalankan

Prasyarat (sudah tersedia di Ubuntu/GNOME):

- Spotify desktop sedang berjalan dan memutar lagu.
- `python3` + PyGObject (`python3-gi`). Kalau belum ada:
  `sudo apt install python3-gi`.

Jalankan:

```bash
cd ~/audio-visual
./run.sh
```

`run.sh` otomatis membuka browser ke <http://127.0.0.1:8765/>. Kalau ingin
tanpa membuka browser:

```bash
python3 server.py
```

Opsi lain: `python3 server.py --port 9000`, `--host 0.0.0.0` (hati-hati:
siapa pun di jaringan bisa mengontrol Spotify lewat API lokal ini).

## Kenapa perlu server kecil?

Browser tidak bisa membaca D-Bus/MPRIS. Jadi `server.py` bertindak sebagai
jembatan lokal:

1. Membaca metadata dan posisi playback Spotify dari
   `org.mpris.MediaPlayer2.spotify` (MPRIS), lalu menghaluskan posisi antar
   pembaruan (interpolasi).
2. Mengambil lirik: folder lokal → **LRCLIB** → **lyrics.ovh**.
3. Menerjemahkan baris lirik ke **English** dan **Indonesia** lewat Google
   Translate (hasilnya di-cache di disk).
4. Menyajikan `index.html`, `romaji.js`, cover album, dan API JSON ke browser.
5. Meneruskan kontrol play/pause, prev/next, dan seek ke Spotify.

Halaman web cukup polling `/api/now-playing` tiap detik, lalu menghitung posisi
lirik sendiri di antara polling supaya highlight tetap mulus.

## Lirik & tab bahasa

Sumber lirik dicoba berurutan:

1. **File lokal** di `~/.local/share/audio-visual/lyrics/` atau
   `~/.local/share/spotify-lyrics-widget/lyrics/` — nama file
   `Artis - Judul.lrc` / `.txt` (LRC memberi sinkronisasi presisi).
2. **LRCLIB** — utamakan `syncedLyrics` (LRC), lalu `plainLyrics`.
3. **lyrics.ovh** — teks polos; timestamp diperkirakan merata sepanjang durasi.

Tab yang muncul (kalau datanya tersedia):

- **Original** — lirik asli.
- **Romaji** — romanisasi Korea/Jepang, dihitung offline oleh `romaji.js`
  (diambil dari proyek `spotify-lyrics-widget`).
- **English** / **Indonesia** — terjemahan Google Translate.

Kalau lirik hanya teks polos, status akan menulis
`perkiraan (tanpa timestamp)`. Gunakan tombol **− / +** di samping tab untuk
menggeser lirik (offset ±0,5 detik, disimpan di browser; klik angka untuk
reset), atau tombol **↻** untuk mengambil ulang lirik.

## Kontrol

| Aksi | Cara |
| --- | --- |
| Ganti mode | Klik emoji, atau tekan `1` 🔥, `2` ❄️, `3` 🌸, `4` 🍁, `5` ☀️, `6` 🌙 |
| Putar / jeda | Tombol ▶ / ❚❚, atau `Spasi` |
| Lagu sebelumnya / berikutnya | Tombol ⏮ / ⏭ |
| Seek | Geser progress bar |
| Mundur / maju 5 detik | `←` / `→` |
| Ganti tab lirik | Klik tab Original / Romaji / English / Indonesia |
| Offset lirik | Tombol − / + (klik angka untuk reset) |

## Catatan & batasan

- Spotify **harus berjalan di mesin yang sama** dan memutar lagu; kalau tidak,
  halaman menampilkan `Spotify tidak terdeteksi`.
- Visualisasi canvas bersifat **simulasi** yang bereaksi pada status
  play/pause. Browser tidak bisa menangkap audio Spotify (DRM), jadi tidak ada
  FFT audio asli seperti versi upload file.
- Lagu tanpa entri LRCLIB akan memakai lyrics.ovh; kalau keduanya kosong,
  tambahkan file `.lrc` lokal agar dapat sinkron presisi.
- Fetch pertama bisa memakan ~5–15 detik (LRCLIB + lyrics.ovh + terjemahan).
  Setelah itu hasilnya di-cache di `~/.cache/audio-visual/lyrics/`.
- Terjemahan butuh internet. Tanpa internet, tab Original/Romaji tetap jalan
  dan lirik lokal tetap bisa dipakai.
- Animasi berat otomatis dikurangi di layar kecil, dan getaran lirik dimatikan
  bila sistem mengaktifkan *prefers-reduced-motion*.

## Struktur

```
audio-visual/
├── index.html   # UI + visualisasi + sinkronisasi lirik (tanpa build step)
├── server.py    # bridge MPRIS, lirik, terjemahan, cache, static server
├── romaji.js    # romanisasi Korea/Jepang (dari spotify-lyrics-widget)
├── run.sh       # jalankan server + buka browser
└── README.md
```

Tidak ada dependensi npm. Font diambil dari Google Fonts; tanpa internet,
browser memakai font cadangan.
