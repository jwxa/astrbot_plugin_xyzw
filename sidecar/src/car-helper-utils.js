const HELPER_MAX_USAGE = 4;

function toObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeString(value) {
  return String(value ?? "").trim();
}

function normalizeNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function normalizeKeyword(value) {
  return normalizeString(value).toLowerCase();
}

function normalizeRoleInfo(raw) {
  const payload = toObject(raw);
  const role = toObject(payload.role);
  return {
    roleId: normalizeString(role.roleId),
    roleName: normalizeString(role.name || role.nickname),
  };
}

function normalizeLegionMembers(raw) {
  const payload = toObject(raw);
  const info = toObject(payload.info || payload.legionData);
  const membersMap = toObject(info.members || payload.members);
  const items = Object.values(membersMap);

  return items
    .map((item) => {
      const member = toObject(item);
      const custom = toObject(member.custom);
      const roleId = normalizeString(member.roleId);
      if (!roleId) {
        return null;
      }
      return {
        roleId,
        name: normalizeString(member.name),
        nickname: normalizeString(member.nickname),
        displayName:
          normalizeString(member.name) ||
          normalizeString(member.nickname) ||
          roleId,
        power: normalizeNumber(member.power ?? custom.s_power),
        redQuench: normalizeNumber(custom.red_quench_cnt),
      };
    })
    .filter(Boolean);
}

function normalizeHelpingCountMap(raw) {
  const payload = toObject(raw);
  const sourceMap = toObject(payload.memberHelpingCntMap || payload.memberhelpingcntmap);
  const result = {};
  for (const [key, value] of Object.entries(sourceMap)) {
    const roleId = normalizeString(key);
    if (!roleId) {
      continue;
    }
    result[roleId] = Math.max(0, Math.trunc(normalizeNumber(value)));
  }
  return result;
}

function normalizeMemberIdFilters(memberIds) {
  if (memberIds === undefined || memberIds === null || memberIds === "") {
    return [];
  }
  const items = Array.isArray(memberIds) ? memberIds : [memberIds];
  return [...new Set(items.map((item) => normalizeString(item)).filter(Boolean))];
}

function matchMemberKeyword(member, keyword) {
  if (!keyword) {
    return true;
  }
  const normalized = normalizeKeyword(keyword);
  if (!normalized) {
    return true;
  }
  return [
    member.roleId,
    member.name,
    member.nickname,
    member.displayName,
  ].some((item) => normalizeKeyword(item).includes(normalized));
}

export function buildCarHelperSnapshot({
  roleInfo,
  legionInfo,
  helpingCountInfo,
  memberIds,
  keyword,
  includeSelf = false,
} = {}) {
  const currentRole = normalizeRoleInfo(roleInfo);
  const helpingCountMap = normalizeHelpingCountMap(helpingCountInfo);
  const requestedMemberIds = normalizeMemberIdFilters(memberIds);
  const members = normalizeLegionMembers(legionInfo)
    .filter((member) => includeSelf || member.roleId !== currentRole.roleId)
    .map((member) => {
      const usedCount = Math.max(0, Math.trunc(normalizeNumber(helpingCountMap[member.roleId])));
      const maxCount = HELPER_MAX_USAGE;
      const availableCount = Math.max(0, maxCount - usedCount);
      return {
        ...member,
        usedCount,
        maxCount,
        availableCount,
        isAvailable: availableCount > 0,
        isFull: usedCount >= maxCount,
        isSelf: member.roleId === currentRole.roleId,
      };
    })
    .sort((left, right) => {
      if (right.redQuench !== left.redQuench) {
        return right.redQuench - left.redQuench;
      }
      if (right.power !== left.power) {
        return right.power - left.power;
      }
      return left.roleId.localeCompare(right.roleId, "zh-Hans-CN");
    });

  const filteredMembers = members.filter((member) => {
    if (requestedMemberIds.length > 0 && !requestedMemberIds.includes(member.roleId)) {
      return false;
    }
    return matchMemberKeyword(member, keyword);
  });

  return {
    currentRole,
    requestedMemberIds,
    keyword: normalizeString(keyword),
    memberHelpingCntMap: helpingCountMap,
    summary: {
      totalMembers: members.length,
      matchedMembers: filteredMembers.length,
      availableMembers: members.filter((member) => member.isAvailable).length,
      exhaustedMembers: members.filter((member) => member.isFull).length,
      maxUsagePerMember: HELPER_MAX_USAGE,
    },
    helpers: filteredMembers,
  };
}
