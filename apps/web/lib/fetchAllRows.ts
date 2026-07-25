type QueryError = {
  message: string
}

type QueryResult<T> = {
  data: T[] | null
  error: QueryError | null
}

export async function fetchAllRows<T>(
  queryPage: (from: number, to: number) => PromiseLike<QueryResult<T>>,
  pageSize = 1000,
) {
  const rows: T[] = []
  let from = 0

  while (true) {
    const to = from + pageSize - 1
    const { data, error } = await queryPage(from, to)
    if (error) {
      return { data: rows, error }
    }

    const page = data ?? []
    rows.push(...page)
    if (page.length < pageSize) {
      return { data: rows, error: null }
    }
    from += pageSize
  }
}
