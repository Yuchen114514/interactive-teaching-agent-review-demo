import { AGENT_MASCOT_POSES, AGENT_MASCOT_SKINS, type AgentMascotSkin, type AgentMascotSkinId, type AgentMascotSkinPalette, type AgentMascotState } from './agentMascot';
import type { ColorScheme } from '../../theme/appearance';

export const DARK_MASCOT_PALETTES: Readonly<Record<AgentMascotSkinId, AgentMascotSkinPalette>> = {
  base: {front:'#30465A', top:'#536E85', side:'#213346', back:'#182737', arm:'#354E63', pixel:'#D5E7F3', accent:'#8FAEC7'},
  terminal: {front:'#193E34', top:'#2D5B4B', side:'#102A23', back:'#0B1F1A', arm:'#254B3C', pixel:'#B9DBA4', accent:'#7CB795'},
  keycap: {front:'#252A30', top:'#424A54', side:'#171C22', back:'#10151A', arm:'#323A43', pixel:'#E4EBF2', accent:'#9EACBA'},
  server: {front:'#2B353B', top:'#46545D', side:'#1B252C', back:'#121B21', arm:'#34424A', pixel:'#D5EAE5', accent:'#86ADA7'},
};

export function resolveMascotAppearance(id: AgentMascotSkinId, scheme: ColorScheme, state: AgentMascotState): AgentMascotSkin {
  const skin=AGENT_MASCOT_SKINS[id];
  if(scheme==='light') {
    if(id!=='base') return skin;
    const pose=AGENT_MASCOT_POSES[state].palette;
    return {...skin, palette:{...skin.palette, ...pose, back:pose.side}};
  }
  const palette=DARK_MASCOT_PALETTES[id];
  return {...skin, palette, indicator:{
    neutral: palette.accent,
    active: id==='terminal' ? '#B5D5A8' : id==='base' ? '#9DC4DF' : '#A6C8BC',
    positive:'#95C9A4', caution:'#D4B57A', danger:'#D99B98', muted:'#8C9AA8',
  }};
}
