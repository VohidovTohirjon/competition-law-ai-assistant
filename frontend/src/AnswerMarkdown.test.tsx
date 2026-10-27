import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AnswerMarkdown } from './AnswerMarkdown'

describe('AI javobining xavfsiz Markdown ko‘rinishi', () => {
  it('qalin matn, ro‘yxat va GFM jadvalini HTMLga aylantiradi', () => {
    const html = renderToStaticMarkup(
      <AnswerMarkdown>{'**19-modda**\n\n- Birinchi band\n\n| Ustun | Qiymat |\n|---|---|\n| A | B |'}</AnswerMarkdown>,
    )
    expect(html).toContain('<strong>19-modda</strong>')
    expect(html).toContain('<li>Birinchi band</li>')
    expect(html).toContain('<table>')
  })

  it('model qaytargan xom HTMLni bajariladigan elementga aylantirmaydi', () => {
    const html = renderToStaticMarkup(<AnswerMarkdown>{'<script>alert(1)</script>'}</AnswerMarkdown>)
    expect(html).not.toContain('<script>')
  })

  it('[1] iqtibosini lentali raqam belgisiga aylantiradi, matnni buzmaydi', () => {
    const html = renderToStaticMarkup(<AnswerMarkdown>{'Ustun mavqe 40 foizdan boshlanadi. [1]\n\n- Band [2]'}</AnswerMarkdown>)
    expect(html).toContain('<sup class="mk" aria-label="Manba 1">1</sup>')
    expect(html).toContain('<sup class="mk" aria-label="Manba 2">2</sup>')
    expect(html).toContain('Ustun mavqe 40 foizdan boshlanadi.')
    expect(html).not.toContain('[1]')
  })
})
