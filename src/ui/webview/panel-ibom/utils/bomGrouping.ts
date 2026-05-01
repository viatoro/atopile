import type { UiBOMData } from "../../../protocol/generated-types";
import type { BomGroup, BomGroupMember } from "./types";

/** Natural sort comparator: R1, R2, R10 — not R1, R10, R2. */
function naturalCompare(a: string, b: string): number {
  const re = /(\d+)|(\D+)/g;
  const aParts = a.match(re) ?? [a];
  const bParts = b.match(re) ?? [b];
  for (let i = 0; i < Math.max(aParts.length, bParts.length); i++) {
    const ap = aParts[i] ?? "";
    const bp = bParts[i] ?? "";
    const an = Number(ap);
    const bn = Number(bp);
    if (!Number.isNaN(an) && !Number.isNaN(bn)) {
      if (an !== bn) return an - bn;
    } else {
      const cmp = ap.localeCompare(bp);
      if (cmp !== 0) return cmp;
    }
  }
  return 0;
}

/**
 * Build BOM groups purely from BOM data (no RenderModel needed).
 *
 * Each UiBOMComponent already represents a unique picked part.
 * Members come from component.usages (each has a designator).
 *
 * Returns:
 *  - groups: BomGroup[] sorted by first designator
 *  - designatorToGroupId: Map from designator → group id (for PCB click → sidebar lookup)
 */
export function buildBomGroups(
  bomData: UiBOMData | null,
): {
  groups: BomGroup[];
  designatorToGroupId: Map<string, string>;
} {
  if (!bomData || bomData.components.length === 0) {
    return { groups: [], designatorToGroupId: new Map() };
  }

  const groups: BomGroup[] = [];
  const designatorToGroupId = new Map<string, string>();

  for (const comp of bomData.components) {
    const groupId = `bom:${comp.id}`;

    const members: BomGroupMember[] = [];
    for (const usage of comp.usages) {
      if (!usage.designator) continue;
      members.push({
        designator: usage.designator,
        address: usage.address,
        line: usage.line,
      });
    }

    if (members.length === 0) continue;

    members.sort((a, b) => naturalCompare(a.designator, b.designator));

    groups.push({
      id: groupId,
      footprintName: comp.type ?? "",
      value: comp.value,
      package: comp.package,
      quantity: members.length,
      members,
      bomComponent: comp,
    });

    for (const member of members) {
      designatorToGroupId.set(member.designator, groupId);
    }
  }

  groups.sort((a, b) => {
    const aFirst = a.members[0]?.designator ?? "";
    const bFirst = b.members[0]?.designator ?? "";
    return naturalCompare(aFirst, bFirst);
  });

  return { groups, designatorToGroupId };
}
