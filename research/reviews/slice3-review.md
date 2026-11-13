# Slice 3 review: photo ingest and photo-to-pulse matching

Commit 5d53881 on pipeline/track1. Files: src/georoute/photos.py, src/georoute/cli.py (match), tests/test_photos.py.
State verified: `uv run pytest` 137 passed, `ruff check` and `ruff format --check` clean.

Verdict: the k-th rule, warm-up handling, per-camera folders, result ordering and the rollover unwrap are correct, and on the real FT1 data
the interval check has a wide margin (see "Measured"). No blocker for FT1 or the 4-camera path. There are several should-fix items where the
code is silent or gives a misleading diagnosis, all reproduced below.

## Measured

- FT1 (432 photos vs 432 cam0 pulses): photo-interval minus pulse-interval, max abs 9.73 ms, p99 8.97 ms, std 3.2 ms, zero intervals above 10 ms.
  This is exactly the 10 ms SubSec truncation bound, so 30 ms is 3x the observed worst case. Pulse intervals: min 0.515 s, median 0.615 s, max 5.34 s (stall).
- Detection on FT1 real intervals: deleting any one photo then appending an extra at the end was detected for all 432 positions. Stray-before-start plus
  last-missing, and first-missing plus extra-last, were also detected (FT1 walking speed varies). Uniform clock drift of 20, 100, 500 ppm passes (correct, the check is per-interval).
- Only 133 of 430 second differences of FT1 pulse intervals exceed 30 ms, so an edge shift is "caught somewhere" only because the walk is irregular (see S1).
- exiftool 13.55: `-ext jpg` is case-insensitive (JPG and jpg both read), does not read `.jpeg`, does not recurse. SubSecTimeOriginal "09" comes out as the string "09" under -json -n (leading zero kept, good).
  exiftool exits 0 even when it prints "Error: Corrupted JPEG image".

## Blocker

None.

## Should-fix

### S1. Shifts at the sequence edges are undetectable when pulse intervals are steady, and the message then points at the wrong place
Evidence (synthetic 20 pulses at exactly 0.6 s, scratchpad sim3.py):
- first photo missing + extra photo after the end: passes silently (counts equal, all intervals equal). Every photo is paired with the wrong pulse.
- stray photo before the session + last photo missing: passes silently.
- stray photo before start only (21 photos, 20 pulses), steady intervals: error says "the sequences agree up to the end, so the difference is at the end". That is false, the extra photo is at the start.
With 12 ms jitter in pulse intervals these were detected, so the hole is driven by how steady the drive is. A vehicle at constant speed with distance-triggering (field test 2) approaches the steady case. The pairing of a missing-plus-extra pair is inherent to the data (no anchor), but it is cheap to narrow:
Fix:
1. Refuse on gaps in Canon FileNumber (consecutive numbers must differ by 1, allowing the folder rollover 9999 -> next folder 0001) and in the unwrapped filename number. A deleted photo is a gap; the only legitimate absence of a photo is a blank delay, which is already excluded from the pulse list. This catches the "missing first/last" side of the pair independently of timing. Name camera and the two neighbouring photos.
2. Reword the end-of-sequence message. When counts differ and no interval diverged, say "cannot tell where; counts differ by N" and name the first extra photo (photos[len(pulses)]) or the last photo when photos are short, rather than claiming the difference is at the end.

### S2. Invalid or missing TM2 pulses are skipped, which hides a shift next to them and can silently disable the check
Evidence: `_check_alignment` does `except SessionError: continue`. Synthetic steady session with pulse 5 TM2-invalid and photo 5 deleted: result is "the sequences agree up to the end, so the difference is at the end" (wrong; with pulse 4 instead of 5 the same deletion is located correctly at IMG_0105). Two intervals touch each invalid pulse and both are dropped, and the shifted tail is again undetectable if steady. If every TM2 row is invalid the check performs zero comparisons and a count match passes with no warning.
Fix: bridge over invalid pulses: keep the index of the last pulse with a valid time and compare `photos[k] - photos[last]` with `pulse[k] - pulse[last]` (tolerance unchanged, or 30 ms per skipped hop is not needed since SubSec error does not accumulate). Count the comparisons made and return it; refuse (or print a loud warning in the CLI) when fewer than, say, half the intervals could be checked. Add tests for an invalid pulse adjacent to a missing photo and for all-invalid.

### S3. exiftool failures are swallowed
Evidence: `subprocess.run(..., capture_output=True)` with no returncode or stderr check. Measured: a truncated JPEG yields "Error: Corrupted JPEG image" on stderr, exit 0, and a record with only FileName, which turns into the misleading "has no EXIF DateTimeOriginal". A chmod 000 file gives a record containing only SourceFile (no FileName, no reason). An unreadable folder or crashed exiftool gives empty stdout, treated as `[]`, so the user sees "cam0: 0 photos for 434 pulses" with no cause. Invalid JSON would be a raw JSONDecodeError traceback. Warnings such as "Bad MakerNotes" are dropped, which silently removes FileNumber and SerialNumber (see S4).
Fix: if returncode != 0 or stderr non-empty, raise MatchError(f"cam{camera}: exiftool reported: {stderr first lines}"); wrap json.loads in try/except; verify len(records) equals the number of files exiftool says it read, or compare against a directory listing to detect `.jpeg`, nested folders and non-JPEG files being ignored (at least refuse when the folder has files that were not read, naming them). Test with a corrupt stub and an unreadable stub.

### S4. Partial FileNumber silently falls back to filename ordering for the whole camera
Evidence: `all(p.file_number is not None ...)` else filename path. Measured: four photos, one with FileNumber missing, orders by filename without comment. A MakerNote warning (S3) is exactly how this would happen, and the fallback is the weaker ordering (needs the clock sort for rollover and cannot see a folder change).
Fix: if some but not all photos have FileNumber, refuse naming the photos that lack it. Keep the pure-filename fallback only when none have it (stubs, non-Canon). Also make the fallback robust: `FILE_NUMBER.search(...).group(1)` raises AttributeError on a name with no digits (measured: "cam0/abc.JPG") - raise MatchError naming the photo.

### S5. Swapped card is only caught when a rig config supplies serials
Evidence: `serials` is never passed by the CLI (rig config is slice 5), so `georoute match` cannot detect a swapped card at all today, and match_photos does not check that a folder is internally consistent.
Fix (no config needed): refuse when one camera folder contains more than one SerialNumber, and when two camera folders carry the same serial; report both when serials differ from config. This catches mixed or duplicated cards today. Also compare serials numerically-normalised (strip leading zeros on both sides): `-n` yields an integer, so `str(r["SerialNumber"])` loses leading zeros that a config string such as "0330304500" will have, giving a false "wrong body" refusal. Not verifiable on real data (see N1).

### S6. Uncaught parse errors and missing camera labels
`datetime.strptime` on a malformed DateTimeOriginal (for example the all-zero camera default) and `float(f"0.{subsec}")` on a non-digit SubSec raise raw ValueError tracebacks, not MatchError, and the message carries no photo. Clock-backwards ("X is earlier on the camera clock than Y"), no-EXIF-tag and the count-mismatch-at-end errors name a path but not the camera, and the end mismatch names no photo. Requirement 4 asks for camera and photo in every error.
Fix: wrap the parse in try/except ValueError -> MatchError(f"{path} DateTimeOriginal {value!r} is not a valid time"); validate SubSec with `isdigit()`; pass the camera into `order_photos` (or catch and prefix `cam{N}:` in match_photos); name the first unmatched photo or pulse at the end.

## Nit

- N1. Canon FileNumber and SerialNumber formats are unverified on real data: photos_exif.tsv has neither tag, and stubs cannot carry MakerNote tags. The assumption `1000407`-style value (folder*10000 + number) from `exiftool -n` and the 9999 -> next folder rollover (1009999 -> 1010001) is consistent with exiftool's documented Canon FileNumber but the only test is record-level with invented values. The test comment/data in test_order_by_canon_file_number (`1010000 + n` for n below 5000) encodes that assumption; state it in the test name or the plan risk list, and verify on the first real 100D photo before field test 2. A card reformat or "auto reset" setting can drop the number from 101-xxxx back to 100-xxxx, which the code reports as a clock-backwards error, a confusing diagnosis; mention both possibilities in the message.
- N2. Equal timestamps are allowed (`<` not `<=`), which is right at 10 ms resolution; the equal case is untested (measured to pass). A zero photo interval would still fail the pulse-interval check, so no gap there.
- N3. 30 ms is sensible: 3x the measured 9.7 ms worst case and 17x smaller than the shortest pulse interval (0.515 s), so a one-photo error produces a ~0.5 s residual. It is not too loose for missing/extra photos in mid-sequence (those are located exactly; verified at all 432 positions) and its tightness is not the limit on detection power, the steadiness of the pulses is (S1). Four-camera data may jitter differently, so print the max residual and the number of intervals checked from `georoute match` (cheap observability, and it will show if the margin ever erodes).
- N4. Count-mismatch with zero pulses (camera took none) prints "the sequences agree up to the end", unhelpful; special-case "cam2 has N photos but no pulse carries its sync edge".
- N5. Non-JPG, `.jpeg`, nested folders and macOS `._` files: the first two are silently not read (only the count mismatch betrays them), and `._IMG` files give "has no EXIF". Covered by S3's unread-file check.
- N6. CLI: exit code 1 for any MatchError including "exiftool is not installed" (arguably 2, tool/environment). Epilog says "1 mismatch". Minor. The summary line lists only cameras with matches, so a camera with no pulses and no folder is invisible; fine, but print "cam1 no pulses".
- N7. Ordering of results `(pulse.n, camera)` is correct and warm-ups are included as specced, but no test asserts the interleaving across cameras (test_four_cameras_with_their_own_gaps asserts counts only).
- N8. A camera that has pulses but no folder is refused, and one with a folder but no pulses is refused, both correct and tested; a folder that exists but is empty while pulses exist is refused via count (tested only implicitly).

## Tests and style

Coverage of the plan's fixtures: 4 cameras with different gaps (yes, counts only), missing photo (yes, locates IMG_0415), extra (yes, count only, location not asserted), missing+extra cancelling (yes), rollover (yes at order_photos level only; never through match_photos), swapped card (record level, wrong-serial only), FT1 integration (yes, 432 matched and a deleted stub located at IMG_0601), CLI (yes, one camera).
Gaps to add: missing first/last photo and stray-before-start with steady intervals (expect refusal, drives S1); invalid TM2 pulse next to a missing photo and all-invalid (S2); corrupt and unreadable stub (S3); partial FileNumber (S4); mixed serials in a folder and the same serial in two folders (S5); malformed DateTimeOriginal (S6); equal timestamps; rollover end-to-end through match_photos with FileNumber set; assertion of cross-camera result ordering; CLI with two cameras. Test realism: `_photos_for` rounds times to 0.01 s rather than truncating and adds no jitter, so quantisation and the tolerance boundary are never exercised; use `floor(t*100)/100` and a random +-9 ms. The two inline `from PIL import Image` imports belong at module level (PIL is a dev dependency). Comments are minimal and appropriate; ruff is clean.
