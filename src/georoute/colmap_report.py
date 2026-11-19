"""Checks a reconstruction against the RTK priors and the measured rig."""

import numpy as np

from georoute.rig import Rig


def prior_residuals(
    priors: dict[str, np.ndarray], centres: dict[str, np.ndarray], fixed: dict[str, bool], strip: str = ""
) -> dict[str, dict]:
    """Reconstructed minus prior camera centre, per camera folder, over RTK-fixed images only. strip
    removes a session prefix, so a multi-session workspace is reported per camera folder within a session."""
    view = {name[len(strip) :]: name for name in priors if name.startswith(strip)}
    out: dict[str, dict] = {}
    for camera in sorted({name.split("/")[0] for name in view}):
        names = [
            n
            for n, full in view.items()
            if n.startswith(f"{camera}/") and full in centres and fixed.get(full)
        ]
        d = np.array([centres[view[n]] - priors[view[n]] for n in names]).reshape(-1, 3)
        h, v = np.hypot(d[:, 0], d[:, 1]), np.abs(d[:, 2])
        stats = {"images": len(names)}
        for label, values in (("horizontal", h), ("vertical", v)):
            for q in (50, 95):
                stats[f"{label}_p{q}_m"] = float(np.percentile(values, q)) if len(values) else None
        out[camera] = stats
    return out


def rig_check(poses: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]], rig: Rig, reference: int) -> dict:
    """Compares where each camera sits relative to the reference camera, pulse by pulse, with the rig config.
    poses maps (pulse, camera) to (world_from_camera rotation, camera centre). The rig gives lens-front
    positions; the optical centre is assumed to lie a common distance behind them along each lens axis,
    and that distance is estimated by least squares."""
    pairs = []
    for (pulse, camera), (r_cam, c_cam) in poses.items():
        if camera == reference or (pulse, reference) not in poses:
            continue
        r_ref, c_ref = poses[(pulse, reference)]
        pairs.append((camera, r_ref.T @ (c_cam - c_ref), r_ref.T @ r_cam))

    ref_from_rig = rig.rig_from_camera(reference).T

    def nominal(camera):
        t = ref_from_rig @ (rig.camera(camera).position_in_rig_m - rig.camera(reference).position_in_rig_m)
        axis = ref_from_rig @ (rig.rig_from_camera(camera)[:, 2] - rig.rig_from_camera(reference)[:, 2])
        return t, axis

    lhs = np.concatenate([-nominal(cam)[1] for cam, _, _ in pairs]) if pairs else np.zeros(0)
    rhs = np.concatenate([v - nominal(cam)[0] for cam, v, _ in pairs]) if pairs else np.zeros(0)
    offset = float(lhs @ rhs / (lhs @ lhs)) if lhs.size and lhs @ lhs > 0 else 0.0

    cameras: dict[str, dict] = {}
    for camera in sorted({cam for cam, _, _ in pairs}):
        t, axis = nominal(camera)
        expected_rotation = ref_from_rig @ rig.rig_from_camera(camera)
        mine = [(v, r) for cam, v, r in pairs if cam == camera]
        translation = [np.linalg.norm(v - (t - offset * axis)) for v, _ in mine]
        angles = [
            np.degrees(np.arccos(np.clip((np.trace(expected_rotation.T @ r) - 1) / 2, -1, 1)))
            for _, r in mine
        ]
        cameras[f"cam{camera}"] = {
            "pulses": len(mine),
            "translation_residual_m": float(np.median(translation)),
            "angle_error_deg": float(np.median(angles)),
        }
    return {"reference": f"cam{reference}", "axial_offset_m": offset, "cameras": cameras}


GATES = {"prior_p95_m": 0.05, "rig_translation_m": 0.02, "rig_angle_deg": 2.0}


MIN_RIG_PULSES = 10


def gate(residuals: dict, rig: dict, expected_cameras: list[str] | None = None) -> list[str]:
    """Reasons the reconstruction fails its gates (empty when it passes). A camera with nothing to check
    fails rather than passing by default."""
    failures = [f"{c} has no registered images" for c in expected_cameras or [] if c not in residuals]
    failures += [
        f"{c} has no RTK-fixed images to check against" for c, r in residuals.items() if not r.get("images")
    ]
    failures += [
        f"{c} shares only {r['pulses']} pulses with {rig.get('reference', 'the reference')}"
        for c, r in rig["cameras"].items()
        if r["pulses"] < MIN_RIG_PULSES
    ]
    for camera, r in residuals.items():
        for axis in ("horizontal", "vertical"):
            value = r.get(f"{axis}_p95_m")
            if value is not None and value > GATES["prior_p95_m"]:
                failures.append(
                    f"{camera} {axis} prior residual p95 {value * 100:.1f} cm exceeds "
                    f"{GATES['prior_p95_m'] * 100:.0f} cm"
                )
    for camera, r in rig["cameras"].items():
        if r["translation_residual_m"] > GATES["rig_translation_m"]:
            failures.append(f"{camera} sits {r['translation_residual_m'] * 100:.1f} cm from its rig position")
        if r["angle_error_deg"] > GATES["rig_angle_deg"]:
            failures.append(f"{camera} points {r['angle_error_deg']:.1f} degrees from its rig orientation")
    return failures


def scale_against_priors(
    priors: dict[str, np.ndarray],
    centres: dict[str, np.ndarray],
    order: dict[str, int],
    camera: str,
    prefix: str = "",
) -> float | None:
    """Reconstructed path length over the prior path length, along one camera's photos in pulse order."""
    names = sorted((n for n in priors if n.startswith(f"{prefix}{camera}/") and n in centres), key=order.get)
    if len(names) < 2:
        return None
    prior_length = sum(np.linalg.norm(priors[b] - priors[a]) for a, b in zip(names, names[1:], strict=False))
    model_length = sum(
        np.linalg.norm(centres[b] - centres[a]) for a, b in zip(names, names[1:], strict=False)
    )
    return float(model_length / prior_length) if prior_length > 0 else None
