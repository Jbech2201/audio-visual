/* Romanisasi lirik — offline, tanpa network.
 *
 *  - Korea   → Revised Romanization (RR)
 *  - Jepang  → Hepburn (modified)
 *
 * Dipakai buat ngGenerate baris "Romaji" kalau provider translate-nya
 * bukan LLM (LLM punya romaji sendiri yang lebih akurat).
 */

// ── Korea: Revised Romanization ────────────────────────────────────────────

// 초성 (choseong) index 0..18 → romaji
// (index 11 = ㅇ yg tidak diucapkan, jadi kosong)
const KO_INITIALS = [
    'g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp',
    's', 'ss', '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h',
];

// 중성 (jungseong) index 0..20 → romaji
const KO_VOWELS = [
    'a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa',
    'wae', 'oe', 'yo', 'u', 'wo', 'we', 'wi', 'yu', 'eu', 'ui', 'i',
];

// 종성 (jongseong) index 0..27 → jamo
const KO_JONG = [
    '', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ',
    'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ',
    'ㅍ', 'ㅎ',
];

// Cara baca 받침 (coda) — dipakai kalau consonant nya gak naik ke syllable berikutnya
// 예: 밥 → "bap", 학교 → "hakgyo"
const KO_CODA = {
    'ㄱ': 'k', 'ㄲ': 'kk', 'ㄴ': 'n', 'ㄷ': 't', 'ㄸ': 'tt', 'ㄹ': 'l',
    'ㅁ': 'm', 'ㅂ': 'p', 'ㅃ': 'pp', 'ㅅ': 't', 'ㅆ': 'tt', 'ㅇ': 'ng',
    'ㅈ': 't', 'ㅉ': 'tt', 'ㅊ': 'ch', 'ㅋ': 'k', 'ㅌ': 't', 'ㅍ': 'p',
    'ㅎ': 't',
};

// Cara baca consonant yg "naik" jadi 초성 syllable berikutnya
// 예: 밥이 → "babi", 강아지 → "gangaji"
const KO_MOVED = {
    'ㄱ': 'g', 'ㄲ': 'kk', 'ㄴ': 'n', 'ㄷ': 'd', 'ㄸ': 'tt', 'ㄹ': 'r',
    'ㅁ': 'm', 'ㅂ': 'b', 'ㅃ': 'pp', 'ㅅ': 's', 'ㅆ': 'ss', 'ㅇ': 'ng',
    'ㅈ': 'j', 'ㅉ': 'jj', 'ㅊ': 'ch', 'ㅋ': 'k', 'ㅌ': 't', 'ㅍ': 'p',
    'ㅎ': 'h',
};

// 쌍받침 → [coda1, coda2]; coda2 bisa naik ke syllable berikutnya
const KO_CLUSTER = {
    'ㄳ': ['ㄱ', 'ㅅ'],
    'ㄵ': ['ㄴ', 'ㅈ'],
    'ㄶ': ['ㄴ', 'ㅎ'],
    'ㄺ': ['ㄹ', 'ㄱ'],
    'ㄻ': ['ㄹ', 'ㅁ'],
    'ㄼ': ['ㄹ', 'ㅂ'],
    'ㄽ': ['ㄹ', 'ㅅ'],
    'ㄾ': ['ㄹ', 'ㅌ'],
    'ㄿ': ['ㄹ', 'ㅍ'],
    'ㅀ': ['ㄹ', 'ㅎ'],
    'ㅄ': ['ㅂ', 'ㅅ'],
};

const HANGUL_BASE = 0xAC00;
const HANGUL_LAST = 0xD7A3;

/**
 * Pecah satu syllable hangul jadi {i, v, f}.
 * @param {string} ch
 * @returns {?{i: number, v: number, f: number}}
 */
function decomposeHangul(ch) {
    const code = ch.codePointAt(0);
    if (code < HANGUL_BASE || code > HANGUL_LAST)
        return null;
    const s = code - HANGUL_BASE;
    return {
        f: s % 28,
        v: Math.floor(s / 28) % 21,
        i: Math.floor(s / (28 * 21)),
    };
}

const SEPARATORS = /[\s,.;:!?'"()\[\]{}<>·\-–—]/;

/**
 * Romanisasi teks Korea (Revised Romanization).
 * @param {string} text
 * @returns {string}
 */
export function romanizeKorean(text) {
    const chars = Array.from(text);
    const tokens = chars.map(ch => {
        const syl = decomposeHangul(ch);
        return syl ? {syl} : {ch};
    });

    const out = [];
    let carry = null; // konsonan yang harus jadi 초성 syllable berikutnya

    for (let idx = 0; idx < tokens.length; idx++) {
        const token = tokens[idx];
        if (!token.syl) {
            out.push(token.ch);
            continue;
        }

        const {i, v, f} = token.syl;

        // syllable berikutnya + apakah ada pemisah (spasi) di antaranya
        let nextSyl = null;
        let separated = false;
        for (let k = idx + 1; k < tokens.length; k++) {
            if (tokens[k].syl) {
                nextSyl = tokens[k].syl;
                break;
            }
            if (tokens[k].ch && SEPARATORS.test(tokens[k].ch))
                separated = true;
        }
        // liaison cuma jalan kalau syllable berikutnya nempel & initial-nya kosong (ㅇ)
        const canLiaison = nextSyl !== null && !separated &&
            KO_INITIALS[nextSyl.i] === '';

        let init = KO_INITIALS[i];
        if (carry && init === '') {
            init = carry; // liaison: 받침 naik jadi 초성
        }
        carry = null;

        let vowel = KO_VOWELS[v];
        if (v === 19) { // ㅢ
            vowel = (i === 0 && f === 0) ? 'eu' : 'ui';
        }

        let finalRoman = '';
        if (f !== 0) {
            const jamo = KO_JONG[f];
            const cluster = KO_CLUSTER[jamo];
            if (cluster) {
                // 쌍받침: coda1 ditulis, coda2 bisa naik
                finalRoman = KO_CODA[cluster[0]] ?? '';
                if (canLiaison)
                    carry = KO_MOVED[cluster[1]] ?? '';
            } else {
                if (canLiaison)
                    carry = KO_MOVED[jamo] ?? ''; // 받침 naik jadi 초성
                else
                    finalRoman = KO_CODA[jamo] ?? '';
            }
        }

        out.push(init + vowel + finalRoman);
    }

    return out.join('');
}

// ── Jepang: Hepburn ───────────────────────────────────────────────────────

// Kana dasar (hiragana & katakana share tabel yang sama)
const JA_BASE = {
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'wi', 'ゑ': 'we', 'を': 'wo', 'ん': 'n',
    'ゔ': 'vu',
};

// Dua huruf (digraph) → romaji
const JA_DIGRAPHS = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo',
    'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
    'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
    'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo',
    'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho',
    'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
    'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
    'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
    'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
    'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
    'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo',
    'ふぁ': 'fa', 'ふぃ': 'fi', 'ふぇ': 'fe', 'ふぉ': 'fo',
    'うぃ': 'wi', 'うぇ': 'we', 'うぉ': 'wo',
    'てぃ': 'ti', 'でぃ': 'di', 'でぇ': 'de', 'どぉ': 'do',
    'すぃ': 'si', 'ずぃ': 'zi', 'つぁ': 'tsa', 'つぃ': 'tsi',
    'つぇ': 'tse', 'つぉ': 'tso', 'しぇ': 'she', 'じぇ': 'je',
    'ちぇ': 'che', 'くゎ': 'kwa', 'ぐゎ': 'gwa', 'ふゅ': 'fyu',
    'ヴぁ': 'va', 'ヴぃ': 'vi', 'ヴぇ': 've', 'ヴぉ': 'vo',
};

const VOWEL_CHARS = new Set(['a', 'i', 'u', 'e', 'o']);

function katakanaToHiragana(ch) {
    const code = ch.codePointAt(0);
    if (code >= 0x30A1 && code <= 0x30F6)
        return String.fromCodePoint(code - 0x60);
    return ch;
}

/**
 * Ubah rangkaian kana (campuran hiragana/katakana) ke hiragana supaya
 * tabel digraph bisa dipakai untuk keduanya.
 * @param {string} text
 * @returns {string}
 */
function normalizeKana(text) {
    return Array.from(text).map(katakanaToHiragana).join('');
}

/**
 * Ubah 1 karakter kana (atau digraph) jadi romaji.
 * @param {string} ch
 * @returns {?string} null kalau bukan kana
 */
function kanaToRomaji(ch) {
    if (JA_DIGRAPHS[ch])
        return JA_DIGRAPHS[ch];
    if (JA_BASE[ch])
        return JA_BASE[ch];
    const hira = katakanaToHiragana(ch);
    if (JA_BASE[hira])
        return JA_BASE[hira];
    if (ch === 'ヴ')
        return 'vu';
    if (ch === 'ヵ')
        return 'ka';
    if (ch === 'ヶ')
        return 'ke';
    if (ch === 'ヮ')
        return 'wa';
    return null;
}

/**
 * Romanisasi teks Jepang (Hepburn).
 * Kanji tidak bisa diromaji offline — dibiarkan apa adanya.
 * @param {string} text
 * @returns {string}
 */
export function romanizeJapanese(text) {
    const chars = Array.from(text);
    const chunks = [];

    // 1) ubah kana jadi potongan romaji
    for (let i = 0; i < chars.length; i++) {
        const ch = chars[i];

        if (ch === 'っ' || ch === 'ッ') {
            chunks.push({type: 'sokuon'});
            continue;
        }

        if (ch === 'ん' || ch === 'ン') {
            chunks.push({type: 'n'});
            continue;
        }

        if (ch === 'ー' || ch === 'ｰ') {
            chunks.push({type: 'long'});
            continue;
        }

        if (ch === '・') {
            chunks.push({type: 'text', value: ' '});
            continue;
        }

        // digraph 2 huruf (hiragana & katakana)
        if (chars[i + 1]) {
            const two = normalizeKana(ch + chars[i + 1]);
            if (JA_DIGRAPHS[two]) {
                chunks.push({type: 'text', value: JA_DIGRAPHS[two]});
                i++;
                continue;
            }
        }

        const r = kanaToRomaji(ch);
        if (r !== null) {
            chunks.push({type: 'text', value: r});
            continue;
        }

        chunks.push({type: 'text', value: ch});
    }

    // 2) rapikan: sokuon (っ), n (ん), chōonpu (ー)
    let out = '';
    for (let i = 0; i < chunks.length; i++) {
        const c = chunks[i];

        if (c.type === 'sokuon') {
            const next = chunks[i + 1];
            if (next && next.type === 'text' && next.value) {
                const first = next.value[0];
                if (VOWEL_CHARS.has(first))
                    out += 'ts';
                else
                    out += first + first;
            } else {
                out += 'ts';
            }
            continue;
        }

        if (c.type === 'long') {
            const m = out.match(/([aeiou])[^aeiou]*$/);
            if (m)
                out += m[1] === 'o' ? 'ou' : m[1];
            else
                out += 'u';
            continue;
        }

        if (c.type === 'n') {
            const next = chunks[i + 1];
            const nv = next && next.type === 'text' ? next.value : '';
            if (nv && (VOWEL_CHARS.has(nv[0]) || nv[0] === 'y'))
                out += "n'";
            else
                out += 'n';
            continue;
        }

        out += c.value;
    }

    return out;
}

/**
 * Deteksi bahasa dari isi teks: 'ko' | 'ja' | 'en'
 * @param {string} text
 * @returns {string}
 */
export function detectLanguage(text) {
    let hangul = 0;
    let kana = 0;
    let kanji = 0;
    let latin = 0;

    for (const ch of text) {
        const code = ch.codePointAt(0);
        if ((code >= 0xAC00 && code <= 0xD7A3) ||
            (code >= 0x1100 && code <= 0x11FF) ||
            (code >= 0x3130 && code <= 0x318F))
            hangul++;
        else if ((code >= 0x3040 && code <= 0x309F) ||
            (code >= 0x30A0 && code <= 0x30FF))
            kana++;
        else if ((code >= 0x4E00 && code <= 0x9FFF) ||
            (code >= 0x3400 && code <= 0x4DBF))
            kanji++;
        else if ((code >= 0x41 && code <= 0x5A) || (code >= 0x61 && code <= 0x7A))
            latin++;
    }

    if (hangul >= 3 && hangul > kana)
        return 'ko';
    if ((kana >= 2 && kana >= hangul) || (kanji >= 2 && kana >= 1))
        return 'ja';
    return 'en';
}

/**
 * Romanisasi otomatis: pilih berdasarkan bahasa teks.
 * @param {string} text
 * @returns {string}
 */
export function romanize(text) {
    if (!text)
        return '';
    switch (detectLanguage(text)) {
    case 'ko':
        return romanizeKorean(text);
    case 'ja':
        return romanizeJapanese(text);
    default:
        return text;
    }
}
