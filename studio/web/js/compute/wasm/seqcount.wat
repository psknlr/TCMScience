;; Exact integer sequence counts with WebAssembly SIMD (128-bit), the same counts as the JavaScript kernel
;; (compute/kernels.js sequenceCounts) and the WebGPU kernel (runtime/webgpu.js).
;;
;; Sequences are laid out by the caller as ASCII letters, each padded with zeros to `stride` bytes (a multiple of
;; 16). For the distance counts `bases` first turns the letters into base codes, A=1 C=2 G=3 T=4 and 0 for everything
;; else (gaps, ambiguity codes, padding); purines (A, G) are odd and pyrimidines (C, T) even, so a difference is a
;; transition when (a xor b) & 1 is 0. Each 16-byte block yields lane masks; i8x16.bitmask and i32.popcnt count them.
;;
;; Build: node studio/scripts/build_wasm.mjs (wabt) writes seqcount.wasm.js; a test checks it matches this file.
(module
  (memory (export "memory") 1)

  ;; letters to base codes in place, `len` bytes (a multiple of 16): A=1 C=2 G=3 T=4, every other byte 0
  (func (export "bases") (param $p i32) (param $len i32)
    (local $k i32) (local $v v128)
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $k) (local.get $len)))
        (local.set $v (v128.load (i32.add (local.get $p) (local.get $k))))
        (v128.store (i32.add (local.get $p) (local.get $k))
          (v128.or
            (v128.or
              (v128.and (i8x16.eq (local.get $v) (i8x16.splat (i32.const 65))) (i8x16.splat (i32.const 1)))
              (v128.and (i8x16.eq (local.get $v) (i8x16.splat (i32.const 67))) (i8x16.splat (i32.const 2))))
            (v128.or
              (v128.and (i8x16.eq (local.get $v) (i8x16.splat (i32.const 71))) (i8x16.splat (i32.const 3)))
              (v128.and (i8x16.eq (local.get $v) (i8x16.splat (i32.const 84))) (i8x16.splat (i32.const 4))))))
        (local.set $k (i32.add (local.get $k) (i32.const 16)))
        (br $next))))

  ;; one row of the distance matrix: the pairs (i, j) for every j > i, written to out as u32
  ;; [sites, differences, transitions] at (i * n + j) * 16 bytes; the caller zeroes out first
  (func (export "distance_row") (param $seq i32) (param $n i32) (param $stride i32) (param $out i32) (param $i i32)
    (local $j i32) (local $k i32) (local $pa i32) (local $pb i32) (local $rec i32)
    (local $sites i32) (local $diffs i32) (local $ts i32)
    (local $a v128) (local $b v128) (local $site v128) (local $diff v128) (local $tr v128)
    (local.set $pa (i32.add (local.get $seq) (i32.mul (local.get $i) (local.get $stride))))
    (local.set $j (i32.add (local.get $i) (i32.const 1)))
    (block $done_j
      (loop $next_j
        (br_if $done_j (i32.ge_u (local.get $j) (local.get $n)))
        (local.set $pb (i32.add (local.get $seq) (i32.mul (local.get $j) (local.get $stride))))
        (local.set $sites (i32.const 0))
        (local.set $diffs (i32.const 0))
        (local.set $ts (i32.const 0))
        (local.set $k (i32.const 0))
        (block $done_k
          (loop $next_k
            (br_if $done_k (i32.ge_u (local.get $k) (local.get $stride)))
            (local.set $a (v128.load (i32.add (local.get $pa) (local.get $k))))
            (local.set $b (v128.load (i32.add (local.get $pb) (local.get $k))))
            ;; a comparable site: both codes are bases
            (local.set $site (v128.and
              (i8x16.ne (local.get $a) (v128.const i8x16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0))
              (i8x16.ne (local.get $b) (v128.const i8x16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0))))
            (local.set $diff (v128.and (local.get $site) (i8x16.ne (local.get $a) (local.get $b))))
            (local.set $tr (v128.and (local.get $diff)
              (i8x16.eq
                (v128.and (v128.xor (local.get $a) (local.get $b)) (v128.const i8x16 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1))
                (v128.const i8x16 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0))))
            (local.set $sites (i32.add (local.get $sites) (i32.popcnt (i8x16.bitmask (local.get $site)))))
            (local.set $diffs (i32.add (local.get $diffs) (i32.popcnt (i8x16.bitmask (local.get $diff)))))
            (local.set $ts (i32.add (local.get $ts) (i32.popcnt (i8x16.bitmask (local.get $tr)))))
            (local.set $k (i32.add (local.get $k) (i32.const 16)))
            (br $next_k)))
        (local.set $rec (i32.add (local.get $out)
          (i32.shl (i32.add (i32.mul (local.get $i) (local.get $n)) (local.get $j)) (i32.const 4))))
        (i32.store (local.get $rec) (local.get $sites))
        (i32.store offset=4 (local.get $rec) (local.get $diffs))
        (i32.store offset=8 (local.get $rec) (local.get $ts))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br $next_j))))

  ;; differing bytes of two sequences of `stride` bytes (raw letters: ambiguity codes and U count as themselves)
  (func (export "hamming") (param $pa i32) (param $pb i32) (param $stride i32) (result i32)
    (local $k i32) (local $d i32)
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $k) (local.get $stride)))
        (local.set $d (i32.add (local.get $d) (i32.popcnt (i8x16.bitmask
          (i8x16.ne (v128.load (i32.add (local.get $pa) (local.get $k))) (v128.load (i32.add (local.get $pb) (local.get $k))))))))
        (local.set $k (i32.add (local.get $k) (i32.const 16)))
        (br $next)))
    (local.get $d))

  ;; G, C and S letters (71, 67, 83) in `stride` bytes
  (func (export "gc") (param $p i32) (param $stride i32) (result i32)
    (local $k i32) (local $c i32) (local $v v128)
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $k) (local.get $stride)))
        (local.set $v (v128.load (i32.add (local.get $p) (local.get $k))))
        (local.set $c (i32.add (local.get $c) (i32.popcnt (i8x16.bitmask (v128.or
          (v128.or (i8x16.eq (local.get $v) (v128.const i8x16 71 71 71 71 71 71 71 71 71 71 71 71 71 71 71 71))
                   (i8x16.eq (local.get $v) (v128.const i8x16 67 67 67 67 67 67 67 67 67 67 67 67 67 67 67 67)))
          (i8x16.eq (local.get $v) (v128.const i8x16 83 83 83 83 83 83 83 83 83 83 83 83 83 83 83 83)))))))
        (local.set $k (i32.add (local.get $k) (i32.const 16)))
        (br $next)))
    (local.get $c))
)
