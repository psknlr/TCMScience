;; Exact Tanimoto search over packed 2048-bit fingerprints with WebAssembly SIMD (128-bit): for each query, the
;; reference most similar to it, as (index, intersection, union) of their bits, the smallest index on ties. The same
;; answer as the JavaScript kernel (compute/science-cpu.js tanimotoBestJS), the WebGPU kernel (compute/science-gpu.js)
;; and Python's bioagent.admet.chem.tanimoto_best.
;;
;; A fingerprint is 256 bytes, laid out by the caller as np.packbits writes a row (compute/science-cpu.js says how the
;; words map to bits; popcounts and AND do not depend on it). i8x16.popcnt counts the bits of each byte; sixteen of
;; them summed per lane stay at most 128, so one i8x16 sum per fingerprint cannot overflow before it is widened.
;;
;; Similarity c / u (c the intersection, u the union) is compared exactly by cross-multiplication, c1 * u2 > c2 * u1,
;; with an empty union scored 0 / 1 (Tanimoto 0, as Python's similarity() gives): products are at most 2^22 for 2048
;; bits. Only a strictly greater similarity replaces the best so far, so the smallest index stands on ties, as
;; np.argmax gives; references are visited in index order, and a later call (the next block of references, at a larger
;; base index) can only replace an answer with a strictly better one.
;;
;; Build: node studio/scripts/build_wasm.mjs (wabt) writes science.wasm.js; a test checks it matches this file.
(module
  (memory (export "memory") 1)

  ;; the sixteen byte lanes of `v` added up: an i8x16 of bit counts as one i32
  (func $hsum (param $v v128) (result i32)
    (local $w v128)
    (local.set $w (i32x4.extadd_pairwise_i16x8_u (i16x8.extadd_pairwise_i8x16_u (local.get $v))))
    (i32.add
      (i32.add (i32x4.extract_lane 0 (local.get $w)) (i32x4.extract_lane 1 (local.get $w)))
      (i32.add (i32x4.extract_lane 2 (local.get $w)) (i32x4.extract_lane 3 (local.get $w)))))

  ;; the popcounts of `n` fingerprints at `src` (256 bytes each), as u32 at `dst`
  (func (export "popcounts") (param $src i32) (param $n i32) (param $dst i32)
    (local $i i32) (local $p i32)
    (local.set $p (local.get $src))
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (i32.store (i32.add (local.get $dst) (i32.shl (local.get $i) (i32.const 2)))
          (call $hsum
            (i8x16.add
              (i8x16.add
                (i8x16.add
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=0 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=16 (local.get $p))))
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=32 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=48 (local.get $p)))))
                (i8x16.add
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=64 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=80 (local.get $p))))
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=96 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=112 (local.get $p))))))
              (i8x16.add
                (i8x16.add
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=128 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=144 (local.get $p))))
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=160 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=176 (local.get $p)))))
                (i8x16.add
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=192 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=208 (local.get $p))))
                  (i8x16.add
                    (i8x16.popcnt (v128.load offset=224 (local.get $p)))
                    (i8x16.popcnt (v128.load offset=240 (local.get $p)))))))))
        (local.set $p (i32.add (local.get $p) (i32.const 256)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  ;; For each of `nq` queries (fingerprint i at q + 256 i, its popcount at qc + 4 i), the best answer so far at
  ;; best + 12 i, three u32: index, intersection, union (index -1 when there is none yet), against `nr` references
  ;; (fingerprint r at refs + 256 r, popcount at rc + 4 r) whose first one has index `base`. The query stays in sixteen
  ;; v128 locals while the references stream past it.
  (func (export "tanimoto_best") (param $q i32) (param $qc i32) (param $nq i32) (param $refs i32) (param $rc i32)
                                 (param $nr i32) (param $base i32) (param $best i32)
    (local $i i32) (local $j i32) (local $p i32) (local $r i32) (local $o i32) (local $a i32)
    (local $c i32) (local $u i32) (local $ce i32) (local $ue i32)
    (local $bi i32) (local $bc i32) (local $bu i32) (local $bce i32) (local $bue i32)
    (local $q0 v128) (local $q1 v128) (local $q2 v128) (local $q3 v128)
    (local $q4 v128) (local $q5 v128) (local $q6 v128) (local $q7 v128)
    (local $q8 v128) (local $q9 v128) (local $q10 v128) (local $q11 v128)
    (local $q12 v128) (local $q13 v128) (local $q14 v128) (local $q15 v128)
    (local.set $p (local.get $q))
    (block $queries_done
      (loop $next_query
        (br_if $queries_done (i32.ge_u (local.get $i) (local.get $nq)))
        (local.set $q0 (v128.load offset=0 (local.get $p)))
        (local.set $q1 (v128.load offset=16 (local.get $p)))
        (local.set $q2 (v128.load offset=32 (local.get $p)))
        (local.set $q3 (v128.load offset=48 (local.get $p)))
        (local.set $q4 (v128.load offset=64 (local.get $p)))
        (local.set $q5 (v128.load offset=80 (local.get $p)))
        (local.set $q6 (v128.load offset=96 (local.get $p)))
        (local.set $q7 (v128.load offset=112 (local.get $p)))
        (local.set $q8 (v128.load offset=128 (local.get $p)))
        (local.set $q9 (v128.load offset=144 (local.get $p)))
        (local.set $q10 (v128.load offset=160 (local.get $p)))
        (local.set $q11 (v128.load offset=176 (local.get $p)))
        (local.set $q12 (v128.load offset=192 (local.get $p)))
        (local.set $q13 (v128.load offset=208 (local.get $p)))
        (local.set $q14 (v128.load offset=224 (local.get $p)))
        (local.set $q15 (v128.load offset=240 (local.get $p)))
        (local.set $a (i32.load (i32.add (local.get $qc) (i32.shl (local.get $i) (i32.const 2)))))
        (local.set $o (i32.add (local.get $best) (i32.mul (local.get $i) (i32.const 12))))
        (local.set $bi (i32.load offset=0 (local.get $o)))
        (local.set $bc (i32.load offset=4 (local.get $o)))
        (local.set $bu (i32.load offset=8 (local.get $o)))
        ;; the best so far as the comparison scores it: an empty union is 0 / 1
        (local.set $bce (select (local.get $bc) (i32.const 0) (local.get $bu)))
        (local.set $bue (select (local.get $bu) (i32.const 1) (local.get $bu)))
        (local.set $r (local.get $refs))
        (local.set $j (i32.const 0))
        (block $refs_done
          (loop $next_ref
            (br_if $refs_done (i32.ge_u (local.get $j) (local.get $nr)))
            (local.set $c
              (call $hsum
                (i8x16.add
                  (i8x16.add
                    (i8x16.add
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q0) (v128.load offset=0 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q1) (v128.load offset=16 (local.get $r)))))
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q2) (v128.load offset=32 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q3) (v128.load offset=48 (local.get $r))))))
                    (i8x16.add
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q4) (v128.load offset=64 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q5) (v128.load offset=80 (local.get $r)))))
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q6) (v128.load offset=96 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q7) (v128.load offset=112 (local.get $r)))))))
                  (i8x16.add
                    (i8x16.add
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q8) (v128.load offset=128 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q9) (v128.load offset=144 (local.get $r)))))
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q10) (v128.load offset=160 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q11) (v128.load offset=176 (local.get $r))))))
                    (i8x16.add
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q12) (v128.load offset=192 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q13) (v128.load offset=208 (local.get $r)))))
                      (i8x16.add
                        (i8x16.popcnt (v128.and (local.get $q14) (v128.load offset=224 (local.get $r))))
                        (i8x16.popcnt (v128.and (local.get $q15) (v128.load offset=240 (local.get $r))))))))))
            (local.set $u (i32.sub (i32.add (local.get $a) (i32.load (i32.add (local.get $rc) (i32.shl (local.get $j) (i32.const 2))))) (local.get $c)))
            (local.set $ce (select (local.get $c) (i32.const 0) (local.get $u)))
            (local.set $ue (select (local.get $u) (i32.const 1) (local.get $u)))
            (if (i32.or (i32.lt_s (local.get $bi) (i32.const 0))
                        (i32.gt_u (i32.mul (local.get $ce) (local.get $bue)) (i32.mul (local.get $bce) (local.get $ue))))
              (then
                (local.set $bi (i32.add (local.get $base) (local.get $j)))
                (local.set $bc (local.get $c)) (local.set $bu (local.get $u))
                (local.set $bce (local.get $ce)) (local.set $bue (local.get $ue))))
            (local.set $r (i32.add (local.get $r) (i32.const 256)))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $next_ref)))
        (i32.store offset=0 (local.get $o) (local.get $bi))
        (i32.store offset=4 (local.get $o) (local.get $bc))
        (i32.store offset=8 (local.get $o) (local.get $bu))
        (local.set $p (i32.add (local.get $p) (i32.const 256)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next_query))))
)
