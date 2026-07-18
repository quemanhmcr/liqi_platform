defmodule LiqiPersistence.Query do
  @moduledoc false
  alias LiqiPersistence.Error

  def one(repo, sql, params, opts \\ []) do
    case Ecto.Adapters.SQL.query(repo, sql, params, opts) do
      {:ok, %{columns: columns, rows: [row]}} ->
        {:ok, row_map(columns, row)}

      {:ok, %{rows: []}} ->
        {:ok, nil}

      {:ok, %{rows: rows}} ->
        {:error,
         %Error{code: :unexpected_row_count, retryable: false, details: %{rows: length(rows)}}}

      {:error, exception} ->
        {:error, Error.from_exception(exception)}
    end
  end

  def all(repo, sql, params, opts \\ []) do
    case Ecto.Adapters.SQL.query(repo, sql, params, opts) do
      {:ok, %{columns: columns, rows: rows}} -> {:ok, Enum.map(rows, &row_map(columns, &1))}
      {:error, exception} -> {:error, Error.from_exception(exception)}
    end
  end

  def scalar(repo, sql, params, opts \\ []) do
    case Ecto.Adapters.SQL.query(repo, sql, params, opts) do
      {:ok, %{rows: [[value]]}} ->
        {:ok, value}

      {:ok, %{rows: rows}} ->
        {:error,
         %Error{code: :unexpected_row_count, retryable: false, details: %{rows: length(rows)}}}

      {:error, exception} ->
        {:error, Error.from_exception(exception)}
    end
  end

  defp row_map(columns, row), do: columns |> Enum.zip(row) |> Map.new()
end
