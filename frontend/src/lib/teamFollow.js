export function createTeamFollowController({
  request,
  onOptimistic,
  onSuccess,
  onRollback,
  onError,
}) {
  const pending = new Set();

  async function follow(team) {
    if (team.followed || pending.has(team.id)) return false;
    pending.add(team.id);
    onOptimistic(team);
    try {
      const response = await request(team.id);
      onSuccess(team, response);
      return true;
    } catch (error) {
      onRollback(team);
      onError(error);
      return false;
    } finally {
      pending.delete(team.id);
    }
  }

  return { follow };
}
