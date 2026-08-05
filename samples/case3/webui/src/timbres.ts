import type { DdspVstModel, RealtimePatch } from './types'

const INSTRUMENT_NAMES_ZH: Record<string, string> = {
  bassoon: '巴松管',
  brass: '铜管乐',
  cello: '大提琴',
  clarinet: '单簧管',
  'concert grand': '音乐会三角钢琴',
  'concert piano': '音乐会钢琴',
  'double bass': '低音提琴',
  flute: '长笛',
  'grand piano': '三角钢琴',
  horn: '圆号',
  melodica: '口风琴',
  oboe: '双簧管',
  piano: '钢琴',
  saxophone: '萨克斯管',
  sitar: '西塔琴',
  strings: '弦乐',
  trombone: '长号',
  trumpet: '小号',
  tuba: '大号',
  viola: '中提琴',
  violin: '小提琴',
  vowels: '元音人声',
  woodwind: '木管乐',
  woodwinds: '木管乐',
}

const PIANO_PATCH_NAMES_ZH: Record<string, string> = {
  'piano.film-fdn-128-96': 'FiLM-FDN 钢琴',
  'piano.film-ir-fullwet-96-64': 'FiLM-IR 混响钢琴',
  'piano.gru-ir-96-64': 'GRU-IR 钢琴',
  'piano.gru-ir-fullwet-96-64': 'GRU-IR 混响钢琴',
}

export function timbreNameZh(name: string | null | undefined): string {
  const value = name?.trim() ?? ''
  if (!value) return '未命名音色'
  const translated = INSTRUMENT_NAMES_ZH[value.toLowerCase()]
  if (translated) return translated
  const numberedTone = /^tone\s+(\d+)$/i.exec(value)
  return numberedTone ? `音色 ${numberedTone[1]}` : value
}

export function realtimePatchNameZh(
  patch: Pick<RealtimePatch, 'patch_id' | 'name'> | null | undefined,
): string {
  if (!patch) return '未选择音色'
  return PIANO_PATCH_NAMES_ZH[patch.patch_id] ?? timbreNameZh(patch.name)
}

export function modelPrecisionNameZh(precision: string): string {
  return ({
    force_fp16: '全半精度',
    mixed_float16: '混合半精度',
  } as Record<string, string>)[precision] ?? precision
}

export function ddspVstTimbreNameZh(
  model: Pick<DdspVstModel, 'instrument' | 'precision'>,
): string {
  return `${timbreNameZh(model.instrument)} · ${modelPrecisionNameZh(model.precision)}`
}
