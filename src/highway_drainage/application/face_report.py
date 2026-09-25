"""Export all audit findings and provenance, independent of the GUI."""

import csv
from pathlib import Path

from highway_drainage.domain.terrain import FaceAudit


def write_face_report(audit: FaceAudit, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["audit_complete", audit.complete])
        writer.writerow(["build_blocked", audit.blocked])
        writer.writerow(["face_overlap_policy", audit.options.overlap_policy])
        writer.writerow(["maximum_partial_overlap_m2", audit.options.max_overlap_area])
        writer.writerow(["maximum_elevation_difference_m", audit.options.max_z_difference])
        writer.writerow(["coordinates", "Imported terrain working CRS, metres"])
        writer.writerow([
            "status", "reason", "first_file", "first_handle", "first_layer",
            "second_file", "second_handle", "second_layer", "overlap_m2",
            "first_triangle_percent", "second_triangle_percent", "maximum_z_difference_m",
            "x", "y", "retained_file", "retained_handle",
        ])
        for finding in audit.findings:
            first, second, kept = (
                audit.faces[audit.owners[i]].reference
                for i in (finding.first, finding.second, finding.retained)
            )
            writer.writerow([
                "midpoint" if audit.options.overlap_policy == "midpoint" else
                "eligible for cleanup" if finding.repairable else "unresolved", finding.reason,
                str(first.path), first.handle, first.layer,
                str(second.path), second.handle, second.layer,
                finding.area, 100 * finding.first_fraction, 100 * finding.second_fraction,
                finding.max_z_difference, finding.x, finding.y,
                str(kept.path) if finding.repairable
                and audit.options.overlap_policy == "strict" else "",
                kept.handle if finding.repairable
                and audit.options.overlap_policy == "strict" else "",
            ])
        for error in audit.errors:
            writer.writerow(["error", error])
