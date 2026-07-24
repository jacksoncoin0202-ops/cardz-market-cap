/* 極簡 QR 編碼器：byte mode、EC level M、自動揀最細 version（1-10），畫落 canvas 用 */
/* 參考 Nayuki QR Code generator (MIT) 嘅精簡移植，淨支援呢個 site 嘅短 URL */

const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x = x * 2 > 255 ? (x * 2) ^ 0x11d : x * 2;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
})();

function gfMul(a: number, b: number): number {
  return a === 0 || b === 0 ? 0 : GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

/* EC level M 每個 version (1-10) 嘅 [ecCodewordsPerBlock, blockGroup] 表 */
interface VersionInfo {
  totalCodewords: number;
  ecPerBlock: number;
  blocks: number[]; // 每組 block 數目
  dataPerBlock: number[]; // 對應每組嘅 data codewords
}

const VERSIONS: VersionInfo[] = [
  { totalCodewords: 26, ecPerBlock: 10, blocks: [1], dataPerBlock: [16] },
  { totalCodewords: 44, ecPerBlock: 16, blocks: [1], dataPerBlock: [28] },
  { totalCodewords: 70, ecPerBlock: 26, blocks: [1], dataPerBlock: [44] },
  { totalCodewords: 100, ecPerBlock: 18, blocks: [2], dataPerBlock: [32] },
  { totalCodewords: 134, ecPerBlock: 24, blocks: [2], dataPerBlock: [43] },
  { totalCodewords: 172, ecPerBlock: 16, blocks: [4], dataPerBlock: [27] },
  { totalCodewords: 196, ecPerBlock: 18, blocks: [4], dataPerBlock: [31] },
  { totalCodewords: 242, ecPerBlock: 22, blocks: [2, 2], dataPerBlock: [38, 39] },
  { totalCodewords: 292, ecPerBlock: 22, blocks: [3, 2], dataPerBlock: [36, 37] },
  { totalCodewords: 346, ecPerBlock: 26, blocks: [4, 1], dataPerBlock: [43, 44] },
];

const ALIGNMENT: Record<number, number[]> = {
  2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
  7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
};

/* GF(256) 多項式除法攞 EC codewords */
function reedSolomon(data: Uint8Array, degree: number): Uint8Array {
  const gen = new Uint8Array(degree);
  gen[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      gen[j] = gfMul(gen[j], root);
      if (j + 1 < degree) gen[j] ^= gen[j + 1];
    }
    root = gfMul(root, 2);
  }
  const result = new Uint8Array(degree);
  for (const byte of data) {
    const factor = byte ^ result[0];
    result.copyWithin(0, 1);
    result[degree - 1] = 0;
    for (let j = 0; j < degree; j++) result[j] ^= gfMul(gen[j], factor);
  }
  return result;
}

/* mask penalty：四條規則，攞最低分嘅 mask */
function penalty(modules: boolean[][], size: number): number {
  let score = 0;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (x + 4 < size && modules[y][x] === modules[y][x + 1] && modules[y][x] === modules[y][x + 2]
        && modules[y][x] === modules[y][x + 3] && modules[y][x] === modules[y][x + 4]) score += 3;
      if (y + 4 < size && modules[y][x] === modules[y + 1][x] && modules[y][x] === modules[y + 2][x]
        && modules[y][x] === modules[y + 3][x] && modules[y][x] === modules[y + 4][x]) score += 3;
    }
  }
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (x + 1 < size && y + 1 < size && modules[y][x] === modules[y][x + 1]
        && modules[y][x] === modules[y + 1][x] && modules[y][x] === modules[y + 1][x + 1]) score += 3;
    }
  }
  const pattern = [true, false, true, true, true, false, true, false, false, false, false];
  const reverse = [...pattern].reverse();
  const matches = (run: boolean[]) => (run.length === 11 && (run.every((v, i) => v === pattern[i]) || run.every((v, i) => v === reverse[i])));
  for (let y = 0; y < size; y++) {
    for (let x = 0; x + 10 < size; x++) if (matches(modules[y].slice(x, x + 11))) score += 40;
  }
  for (let x = 0; x < size; x++) {
    for (let y = 0; y + 10 < size; y++) if (matches([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => modules[y + i][x]))) score += 40;
  }
  let dark = 0;
  for (const row of modules) for (const cell of row) if (cell) dark++;
  const ratio = (dark * 20) / (size * size);
  score += Math.floor(Math.abs(ratio - 10)) * 10;
  return score;
}

/* 輸出 module matrix（true = 黑格），失敗（字串太長）回傳 null */
export function qrMatrix(text: string): boolean[][] | null {
  const bytes = new TextEncoder().encode(text);
  const versionIndex = VERSIONS.findIndex((v, i) => {
    const dataCodewords = v.blocks.reduce((sum, count, g) => sum + count * v.dataPerBlock[g], 0);
    const capacityBits = dataCodewords * 8;
    const countBits = i + 1 <= 9 ? 8 : 16;
    return 4 + countBits + bytes.length * 8 <= capacityBits;
  });
  if (versionIndex === -1) return null;
  const version = versionIndex + 1;
  const info = VERSIONS[versionIndex];
  const size = 17 + version * 4;
  const dataCodewords = info.blocks.reduce((sum, count, g) => sum + count * info.dataPerBlock[g], 0);
  const countBits = version <= 9 ? 8 : 16;

  /* bit buffer：mode(0100) + count + data + terminator + pad */
  const bits: number[] = [];
  const push = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4);
  push(bytes.length, countBits);
  for (const byte of bytes) push(byte, 8);
  const capacity = dataCodewords * 8;
  push(0, Math.min(4, capacity - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);
  const data = new Uint8Array(dataCodewords);
  for (let i = 0; i < bits.length / 8; i++) {
    let byte = 0;
    for (let b = 0; b < 8; b++) byte = (byte << 1) | bits[i * 8 + b];
    data[i] = byte;
  }
  for (let i = bits.length / 8, pad = 0; i < dataCodewords; i++, pad++) data[i] = pad % 2 === 0 ? 0xec : 0x11;

  /* 切 blocks + EC，再 interleave */
  const dataBlocks: Uint8Array[] = [];
  const ecBlocks: Uint8Array[] = [];
  let offset = 0;
  info.blocks.forEach((count, g) => {
    for (let b = 0; b < count; b++) {
      const block = data.slice(offset, offset + info.dataPerBlock[g]);
      offset += info.dataPerBlock[g];
      dataBlocks.push(block);
      ecBlocks.push(reedSolomon(block, info.ecPerBlock));
    }
  });
  const all: number[] = [];
  const maxData = Math.max(...info.dataPerBlock);
  for (let i = 0; i < maxData; i++) for (const block of dataBlocks) if (i < block.length) all.push(block[i]);
  for (let i = 0; i < info.ecPerBlock; i++) for (const block of ecBlocks) all.push(block[i]);

  /* 鋪 function patterns */
  const modules: boolean[][] = Array.from({ length: size }, () => Array<boolean>(size).fill(false));
  const isFunction: boolean[][] = Array.from({ length: size }, () => Array<boolean>(size).fill(false));
  const set = (x: number, y: number, dark: boolean) => { modules[y][x] = dark; isFunction[y][x] = true; };
  const finder = (cx: number, cy: number) => {
    for (let dy = -1; dy <= 7; dy++) {
      for (let dx = -1; dx <= 7; dx++) {
        const x = cx + dx, y = cy + dy;
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const inOuter = dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6;
        const dark = inOuter && (dx === 0 || dx === 6 || dy === 0 || dy === 6 || (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4));
        set(x, y, dark);
      }
    }
  };
  finder(0, 0); finder(size - 7, 0); finder(0, size - 7);
  for (let i = 8; i < size - 8; i++) { set(i, 6, i % 2 === 0); set(6, i, i % 2 === 0); }
  const align = ALIGNMENT[version] ?? [];
  for (const ay of align) {
    for (const ax of align) {
      if (isFunction[ay][ax]) continue;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }
  /* format info 預留位（之後填） */
  for (let i = 0; i <= 5; i++) set(8, i, false);
  set(8, 7, false); set(8, 8, false); set(7, 8, false);
  for (let i = 9; i < 15; i++) set(14 - i, 8, false);
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, false);
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, false);
  set(8, size - 8, true); // dark module
  /* version info（v7+） */
  if (version >= 7) {
    let rem = version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ (((rem >>> 11) & 1) * 0x1f25);
    const bits18 = (version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const bit = ((bits18 >>> i) & 1) !== 0;
      const a = size - 11 + (i % 3), b = Math.floor(i / 3);
      set(a, b, bit); set(b, a, bit);
    }
  }

  /* zigzag 鋪 data bits */
  const dataBits = all.flatMap((byte) => Array.from({ length: 8 }, (_, i) => ((byte >>> (7 - i)) & 1) !== 0));
  let bitIndex = 0;
  let upward = true;
  for (let x = size - 1; x >= 1; x -= 2) {
    if (x === 6) x = 5;
    for (let i = 0; i < size; i++) {
      const y = upward ? size - 1 - i : i;
      for (const xx of [x, x - 1]) {
        if (isFunction[y][xx]) continue;
        modules[y][xx] = bitIndex < dataBits.length ? dataBits[bitIndex] : false;
        bitIndex++;
      }
    }
    upward = !upward;
  }

  /* 揀最佳 mask（EC level M = 0） */
  const maskFns = [
    (x: number, y: number) => (x + y) % 2 === 0,
    (_x: number, y: number) => y % 2 === 0,
    (x: number) => x % 3 === 0,
    (x: number, y: number) => (x + y) % 3 === 0,
    (x: number, y: number) => (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0,
    (x: number, y: number) => ((x * y) % 2) + ((x * y) % 3) === 0,
    (x: number, y: number) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
    (x: number, y: number) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
  ];
  let best: boolean[][] | null = null;
  let bestScore = Infinity;
  let bestMask = 0;
  for (let mask = 0; mask < 8; mask++) {
    const trial = modules.map((row, y) => row.map((cell, x) => (isFunction[y][x] ? cell : cell !== maskFns[mask](x, y))));
    const score = penalty(trial, size);
    if (score < bestScore) { bestScore = score; best = trial; bestMask = mask; }
  }
  /* format bits：EC level M (0) + mask，BCH(15,5) */
  const format = bestMask; // M = 0b00
  let frem = format;
  for (let i = 0; i < 10; i++) frem = (frem << 1) ^ (((frem >>> 9) & 1) * 0x537);
  const formatBits = (((format << 10) | frem) ^ 0x5412) & 0x7fff;
  const fbit = (i: number) => ((formatBits >>> i) & 1) !== 0;
  for (let i = 0; i <= 5; i++) best![i][8] = fbit(i);
  best![7][8] = fbit(6); best![8][8] = fbit(7); best![8][7] = fbit(8);
  for (let i = 9; i < 15; i++) best![8][14 - i] = fbit(i);
  for (let i = 0; i < 8; i++) best![8][size - 1 - i] = fbit(i);
  for (let i = 8; i < 15; i++) best![size - 15 + i][8] = fbit(i);
  best![size - 8][8] = true;
  return best;
}

/* 畫 QR 落 canvas，置中喺 (cx, cy)，box 尺寸 boxSize（含 quiet zone） */
export function drawQr(ctx: CanvasRenderingContext2D, text: string, cx: number, cy: number, boxSize: number, darkColor: string, lightColor: string): void {
  const matrix = qrMatrix(text);
  if (!matrix) return;
  const count = matrix.length;
  const quiet = 2;
  const moduleSize = boxSize / (count + quiet * 2);
  const ox = cx - boxSize / 2;
  const oy = cy - boxSize / 2;
  ctx.fillStyle = lightColor;
  ctx.fillRect(ox, oy, boxSize, boxSize);
  ctx.fillStyle = darkColor;
  for (let y = 0; y < count; y++) {
    for (let x = 0; x < count; x++) {
      if (matrix[y][x]) ctx.fillRect(ox + (x + quiet) * moduleSize, oy + (y + quiet) * moduleSize, Math.ceil(moduleSize * 10) / 10, Math.ceil(moduleSize * 10) / 10);
    }
  }
}
