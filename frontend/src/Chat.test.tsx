// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Chat } from './App'

const status = {
  legal_ready: true,
  general_ready: true,
  status: 'ready',
  message: 'Huquqiy yordam tayyor',
}

function json(body: unknown, responseStatus = 200) {
  return new Response(JSON.stringify(body), {
    status: responseStatus,
    headers: { 'Content-Type': 'application/json' },
  })
}

function result(answer: string) {
  return {
    history_id: 'history-1', answer, sources: [], result_kind: 'ok', warning: null,
    effective_mode: 'legal', routed_to_legal: false, export_url: null,
  }
}

// The citation "[1]" is rendered as a ribboned numeral element, so the paragraph is matched by its full text.
const answerText = (text: string) => (_: string, element: Element | null) => element?.tagName === 'P' && element.textContent === text

describe('Chat reliability states', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it.each([
    [429, 'Groq so‘rovlar limiti tugadi. Birozdan keyin qayta urinib ko‘ring.', 'Groq so‘rovlar limiti tugadi'],
    [503, 'AI xizmatidan vaqtincha javob olinmadi. Qayta urinib ko‘ring.', 'AI xizmatidan vaqtincha javob olinmadi'],
    [502, 'AI xizmati bo‘sh javob qaytardi.', 'AI xizmati bo‘sh javob qaytardi'],
  ])('keeps provider failure %s visible, ends loading and offers retry', async (responseStatus, detail, expected) => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
      if ((options?.method || 'GET') === 'POST') {
        return json({ detail }, responseStatus)
      }
      return json(status)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Chat />)

    await screen.findByText('Huquqiy yordam tayyor')
    await user.type(screen.getByRole('textbox'), '19-modda nimani belgilaydi?')
    await user.click(screen.getByRole('button', { name: 'Savol yuborish' }))

    expect((await screen.findByRole('alert')).textContent).toContain(expected)
    expect((screen.getByRole('button', { name: 'Qayta urinish' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Savol yuborish' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('ignores a cancelled request A even if it resolves after request B', async () => {
    let resolveA!: (response: Response) => void
    const delayedA = new Promise<Response>(resolve => { resolveA = resolve })
    let postCount = 0
    const fetchMock = vi.fn((_input: RequestInfo | URL, options?: RequestInit) => {
      if ((options?.method || 'GET') !== 'POST') return Promise.resolve(json(status))
      postCount += 1
      return postCount === 1 ? delayedA : Promise.resolve(json(result('B javobi [1].')))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Chat />)

    await screen.findByText('Huquqiy yordam tayyor')
    const input = screen.getByRole('textbox')
    await user.type(input, 'A savol')
    await user.click(screen.getByRole('button', { name: 'Savol yuborish' }))
    await user.click(await screen.findByRole('button', { name: 'Bekor qilish' }))
    expect(screen.getByRole('alert').textContent).toContain('So‘rov bekor qilindi')

    await user.clear(input)
    await user.type(input, 'B savol')
    await user.click(screen.getByRole('button', { name: 'Savol yuborish' }))
    expect(await screen.findByText(answerText('B javobi 1.'))).not.toBeNull()

    resolveA(json(result('A kechikkan javobi.')))
    await waitFor(() => expect(screen.queryByText('A kechikkan javobi.')).toBeNull())
    expect(screen.getByText(answerText('B javobi 1.'))).not.toBeNull()
  })

  it('sends the explicitly selected mode so the backend cannot re-route it', async () => {
    const bodies: string[] = []
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
      if ((options?.method || 'GET') === 'POST') {
        bodies.push(String(options?.body))
        return json(result('Javob'))
      }
      return json(status)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<Chat />)
    await screen.findByText('Huquqiy yordam tayyor')

    // Legal tab is the default.
    await user.type(screen.getByRole('textbox'), 'Ustun mavqe qanday aniqlanadi?')
    await user.click(screen.getByRole('button', { name: 'Savol yuborish' }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(JSON.parse(bodies[0]).mode).toBe('legal')

    // Switching to "Umumiy savol" must send general, even for legal-sounding text.
    await user.click(screen.getByRole('button', { name: 'Umumiy savol' }))
    await screen.findByText('AI yordamchi tayyor')
    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), 'O‘zbekiston Konstitutsiyasida nechta modda bor?')
    await user.click(screen.getByRole('button', { name: 'Savol yuborish' }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(JSON.parse(bodies[1]).mode).toBe('general')
  })
})
