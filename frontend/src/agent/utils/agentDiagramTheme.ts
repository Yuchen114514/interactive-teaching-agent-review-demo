import type { ColorScheme } from '@/theme/appearance';

// The server owns SVG geometry. Only its fixed semantic-renderer palette changes.
const lightPalette: Record<string, string> = {
  '#0d1725': '#f3f6f8', '#152337': '#ffffff', '#17283b': '#edf2f5',
  '#1b5864': '#c9e7e3', '#1c5862': '#c9e7e3', '#1c9a98': '#91ccc8',
  '#202a3a': '#a8b4bf', '#263b53': '#e7eef2', '#283c54': '#e7eef2',
  '#2e435a': '#dce5ea', '#31465e': '#d5dfe6', '#3a708f': '#a8cadb',
  '#536e88': '#9eb4c0', '#607b97': '#7893a6', '#66809a': '#7592a3',
  '#718ca8': '#7895a5', '#7494b3': '#6d8da3', '#75dec6': '#238f7e',
  '#7791a9': '#68889c', '#7895ad': '#68899e', '#78b9ba': '#3d9794',
  '#7dd2be': '#268f7b', '#7ddab7': '#228e72', '#7ed5c6': '#348f80',
  '#8096ac': '#748a9b', '#8ca5bd': '#6d8799', '#8cc5d3': '#4d96a8',
  '#8ea6c0': '#657d8f', '#8fcbe7': '#3d8eae', '#8fd8c2': '#2c9679',
  '#91a7bd': '#6b8091', '#92a9bf': '#657f90', '#92aac2': '#637e91',
  '#9bb0c6': '#71889b', '#9caabb': '#758797', '#9ce9ce': '#278c6d',
  '#9db7cb': '#6a879a', '#9fc1d6': '#608fa7', '#a2b7ca': '#6d8799',
  '#a3b9cb': '#6e8999', '#a9343e': '#f5c6ca', '#a9ecd0': '#258a69',
  '#b8cbe0': '#597b94', '#bba4e6': '#8a66aa', '#c0d4e6': '#4e6576',
  '#d6a9f0': '#9161a8', '#e7bf7d': '#986b30', '#eaf3ff': '#263746',
  '#edc37f': '#9b6b2c', '#ef9b9b': '#b64d55', '#ff9a9c': '#b94550',
};

export function themeDiagramSvg(svg: string, scheme: ColorScheme): string {
  if (scheme === 'dark') return svg;
  return svg.replace(/\b(fill|stroke)="(#[0-9a-f]{6})"/gi, (attribute, _name: string, value: string) =>
    attribute.replace(value, lightPalette[value.toLowerCase()] || value));
}

export function diagramSvgAspectRatio(svg: string): number | null {
  const values = svg.match(/\bviewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)\s*"/i);
  if (!values) return null;
  const width = Number(values[1]);
  const height = Number(values[2]);
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
    ? width / height : null;
}
