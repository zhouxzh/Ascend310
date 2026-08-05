import { describe, expect, it } from 'vitest'
import { ddspVstTimbreNameZh, realtimePatchNameZh, timbreNameZh } from './timbres'

describe('Chinese timbre names', () => {
  it('translates every published MIDI-DDSP and DDSP-VST instrument', () => {
    expect([
      'Violin', 'Viola', 'Cello', 'Double Bass', 'Flute', 'Oboe', 'Clarinet',
      'Saxophone', 'Bassoon', 'Trumpet', 'Horn', 'Trombone', 'Tuba', 'Melodica',
      'Sitar', 'Vowels',
    ].map(timbreNameZh)).toEqual([
      '小提琴', '中提琴', '大提琴', '低音提琴', '长笛', '双簧管', '单簧管',
      '萨克斯管', '巴松管', '小号', '圆号', '长号', '大号', '口风琴',
      '西塔琴', '元音人声',
    ])
  })

  it('gives Piano-DDSP patches stable Chinese display names', () => {
    expect(realtimePatchNameZh({
      patch_id: 'piano.gru-ir-fullwet-96-64',
      name: 'GRU IR Full-Wet 96/64',
    })).toBe('GRU-IR 混响钢琴')
    expect([
      'piano.film-fdn-128-96',
      'piano.film-ir-fullwet-96-64',
      'piano.gru-ir-96-64',
      'piano.gru-ir-fullwet-96-64',
    ].map((patchId) => realtimePatchNameZh({ patch_id: patchId, name: patchId })))
      .toEqual(['FiLM-FDN 钢琴', 'FiLM-IR 混响钢琴', 'GRU-IR 钢琴', 'GRU-IR 混响钢琴'])
    expect(realtimePatchNameZh({ patch_id: 'piano.other', name: 'Concert Grand' }))
      .toBe('音乐会三角钢琴')
  })

  it('localizes numbered fallback tones without changing their IDs', () => {
    expect(timbreNameZh('Tone 7')).toBe('音色 7')
  })

  it('distinguishes duplicate DDSP-VST instruments by Chinese precision names', () => {
    expect(ddspVstTimbreNameZh({ instrument: 'Violin', precision: 'mixed_float16' }))
      .toBe('小提琴 · 混合半精度')
    expect(ddspVstTimbreNameZh({ instrument: 'Violin', precision: 'force_fp16' }))
      .toBe('小提琴 · 全半精度')
  })
})
