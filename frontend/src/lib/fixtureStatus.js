const exceptionalLabels = {
  interrupted: 'Interrupted',
  time_to_be_defined: 'Time to be defined',
  awarded: 'Awarded',
  walkover: 'Walkover',
  suspended: 'Suspended',
  postponed: 'Postponed',
  cancelled: 'Cancelled',
  abandoned: 'Abandoned',
};

export function exceptionalStatusLabel(status) {
  if (status === 'scheduled' || status === 'live' || status === 'finished') return null;
  return Object.hasOwn(exceptionalLabels, status) ? exceptionalLabels[status] : 'Unknown';
}
