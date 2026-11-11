export function pointInImage(clientX, clientY, bounds) {
  const clamp = (value) => Math.max(0, Math.min(1, value));
  return [clamp((clientX - bounds.left) / bounds.width), clamp((clientY - bounds.top) / bounds.height)];
}

export function rectangleBetween(start, end) {
  return [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])];
}

export function visibleRectangle(rect, bounds) {
  return (rect[2] - rect[0]) * bounds.width >= 3 && (rect[3] - rect[1]) * bounds.height >= 3;
}
