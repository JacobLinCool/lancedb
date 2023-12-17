#  Copyright 2023 LanceDB Developers
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Literal, Optional, Type, Union

import deprecation
import numpy as np
import pyarrow as pa
import pydantic

from . import __version__
from .common import VECTOR_COLUMN_NAME
from .util import safe_import_pandas

if TYPE_CHECKING:
    from .pydantic import LanceModel

pd = safe_import_pandas()


class Query(pydantic.BaseModel):
    """The LanceDB Query

    Attributes
    ----------
    vector : List[float]
        the vector to search for
    filter : Optional[str]
        sql filter to refine the query with, optional
    prefilter : bool
        if True then apply the filter before vector search
    k : int
        top k results to return
    metric : str
        the distance metric between a pair of vectors,

        can support L2 (default), Cosine and Dot.
        [metric definitions][search]
    columns : Optional[List[str]]
        which columns to return in the results
    nprobes : int
        The number of probes used - optional

        - A higher number makes search more accurate but also slower.

        - See discussion in [Querying an ANN Index][querying-an-ann-index] for
          tuning advice.
    refine_factor : Optional[int]
        Refine the results by reading extra elements and re-ranking them in memory - optional

        - A higher number makes search more accurate but also slower.

        - See discussion in [Querying an ANN Index][querying-an-ann-index] for
          tuning advice.
    """

    vector_column: str = VECTOR_COLUMN_NAME

    # vector to search for
    vector: List[float]

    # sql filter to refine the query with
    filter: Optional[str] = None

    # if True then apply the filter before vector search
    prefilter: bool = False

    # top k results to return
    k: int

    # # metrics
    metric: str = "L2"

    # which columns to return in the results
    columns: Optional[List[str]] = None

    # optional query parameters for tuning the results,
    # e.g. `{"nprobes": "10", "refine_factor": "10"}`
    nprobes: int = 10

    # Refine factor.
    refine_factor: Optional[int] = None

    with_row_id: bool = False


class LanceQueryBuilder(ABC):
    """Build LanceDB query based on specific query type:
    vector or full text search.
    """

    @classmethod
    def create(
        cls,
        table: "lancedb.table.Table",
        query: Optional[Union[np.ndarray, str, "PIL.Image.Image"]],
        query_type: str,
        vector_column_name: str,
    ) -> LanceQueryBuilder:
        if query is None:
            return LanceEmptyQueryBuilder(table)

        if query_type == "hybrid":
            # hybrid fts and vector query
            return LanceHybridQueryBuilder(table, query, vector_column_name)

        # convert "auto" query_type to "vector" or "fts"
        # and convert the query to vector if needed
        query, query_type = cls._resolve_query(
            table, query, query_type, vector_column_name
        )

        if isinstance(query, str):
            # fts
            return LanceFtsQueryBuilder(table, query)

        if isinstance(query, list):
            query = np.array(query, dtype=np.float32)
        elif isinstance(query, np.ndarray):
            query = query.astype(np.float32)
        else:
            raise TypeError(f"Unsupported query type: {type(query)}")

        return LanceVectorQueryBuilder(table, query, vector_column_name)

    @classmethod
    def _resolve_query(cls, table, query, query_type, vector_column_name):
        # If query_type is fts, then query must be a string.
        # otherwise raise TypeError
        if query_type == "fts":
            if not isinstance(query, str):
                raise TypeError(f"'fts' queries must be a string: {type(query)}")
            return query, query_type
        elif query_type == "vector":
            query = cls._query_to_vector(table, query, vector_column_name)
            return query, query_type
        elif query_type == "auto":
            if isinstance(query, (list, np.ndarray)):
                return query, "vector"
            else:
                conf = table.embedding_functions.get(vector_column_name)
                if conf is not None:
                    query = conf.function.compute_query_embeddings_with_retry(query)[0]
                    return query, "vector"
                else:
                    return query, "fts"
        else:
            raise ValueError(
                f"Invalid query_type, must be 'vector', 'fts', or 'auto': {query_type}"
            )

    @classmethod
    def _query_to_vector(cls, table, query, vector_column_name):
        if isinstance(query, (list, np.ndarray)):
            return query
        conf = table.embedding_functions.get(vector_column_name)
        if conf is not None:
            return conf.function.compute_query_embeddings_with_retry(query)[0]
        else:
            msg = f"No embedding function for {vector_column_name}"
            raise ValueError(msg)

    def __init__(self, table: "lancedb.table.Table"):
        self._table = table
        self._limit = 10
        self._columns = None
        self._where = None
        self._with_row_id = False

    @deprecation.deprecated(
        deprecated_in="0.3.1",
        removed_in="0.4.0",
        current_version=__version__,
        details="Use to_pandas() instead",
    )
    def to_df(self) -> "pd.DataFrame":
        """
        *Deprecated alias for `to_pandas()`. Please use `to_pandas()` instead.*

        Execute the query and return the results as a pandas DataFrame.
        In addition to the selected columns, LanceDB also returns a vector
        and also the "_distance" column which is the distance between the query
        vector and the returned vector.
        """
        return self.to_pandas()

    def to_pandas(self) -> "pd.DataFrame":
        """
        Execute the query and return the results as a pandas DataFrame.
        In addition to the selected columns, LanceDB also returns a vector
        and also the "_distance" column which is the distance between the query
        vector and the returned vector.
        """
        return self.to_arrow().to_pandas()

    @abstractmethod
    def to_arrow(self) -> pa.Table:
        """
        Execute the query and return the results as an
        [Apache Arrow Table](https://arrow.apache.org/docs/python/generated/pyarrow.Table.html#pyarrow.Table).

        In addition to the selected columns, LanceDB also returns a vector
        and also the "_distance" column which is the distance between the query
        vector and the returned vectors.
        """
        raise NotImplementedError

    def to_list(self) -> List[dict]:
        """
        Execute the query and return the results as a list of dictionaries.

        Each list entry is a dictionary with the selected column names as keys,
        or all table columns if `select` is not called. The vector and the "_distance"
        fields are returned whether or not they're explicitly selected.
        """
        return self.to_arrow().to_pylist()

    def to_pydantic(self, model: Type[LanceModel]) -> List[LanceModel]:
        """Return the table as a list of pydantic models.

        Parameters
        ----------
        model: Type[LanceModel]
            The pydantic model to use.

        Returns
        -------
        List[LanceModel]
        """
        return [
            model(**{k: v for k, v in row.items() if k in model.field_names()})
            for row in self.to_arrow().to_pylist()
        ]

    def limit(self, limit: int) -> LanceQueryBuilder:
        """Set the maximum number of results to return.

        Parameters
        ----------
        limit: int
            The maximum number of results to return.

        Returns
        -------
        LanceQueryBuilder
            The LanceQueryBuilder object.
        """
        self._limit = limit
        return self

    def select(self, columns: list) -> LanceQueryBuilder:
        """Set the columns to return.

        Parameters
        ----------
        columns: list
            The columns to return.

        Returns
        -------
        LanceQueryBuilder
            The LanceQueryBuilder object.
        """
        self._columns = columns
        return self

    def where(self, where: str, prefilter: bool = False) -> LanceQueryBuilder:
        """Set the where clause.

        Parameters
        ----------
        where: str
            The where clause which is a valid SQL where clause. See
            `Lance filter pushdown <https://lancedb.github.io/lance/read_and_write.html#filter-push-down>`_
            for valid SQL expressions.
        prefilter: bool, default False
            If True, apply the filter before vector search, otherwise the
            filter is applied on the result of vector search.
            This feature is **EXPERIMENTAL** and may be removed and modified
            without warning in the future.

        Returns
        -------
        LanceQueryBuilder
            The LanceQueryBuilder object.
        """
        self._where = where
        self._prefilter = prefilter
        return self

    def with_row_id(self, with_row_id: bool) -> LanceQueryBuilder:
        """Set whether to return row ids.

        Parameters
        ----------
        with_row_id: bool
            If True, return _rowid column in the results.

        Returns
        -------
        LanceQueryBuilder
            The LanceQueryBuilder object.
        """
        self._with_row_id = with_row_id
        return self


class LanceVectorQueryBuilder(LanceQueryBuilder):
    """
    Examples
    --------
    >>> import lancedb
    >>> data = [{"vector": [1.1, 1.2], "b": 2},
    ...         {"vector": [0.5, 1.3], "b": 4},
    ...         {"vector": [0.4, 0.4], "b": 6},
    ...         {"vector": [0.4, 0.4], "b": 10}]
    >>> db = lancedb.connect("./.lancedb")
    >>> table = db.create_table("my_table", data=data)
    >>> (table.search([0.4, 0.4])
    ...       .metric("cosine")
    ...       .where("b < 10")
    ...       .select(["b"])
    ...       .limit(2)
    ...       .to_pandas())
       b      vector  _distance
    0  6  [0.4, 0.4]        0.0
    """

    def __init__(
        self,
        table: "lancedb.table.Table",
        query: Union[np.ndarray, list, "PIL.Image.Image"],
        vector_column: str = VECTOR_COLUMN_NAME,
    ):
        super().__init__(table)
        self._query = query
        self._metric = "L2"
        self._nprobes = 20
        self._refine_factor = None
        self._vector_column = vector_column
        self._prefilter = False

    def metric(self, metric: Literal["L2", "cosine"]) -> LanceVectorQueryBuilder:
        """Set the distance metric to use.

        Parameters
        ----------
        metric: "L2" or "cosine"
            The distance metric to use. By default "L2" is used.

        Returns
        -------
        LanceVectorQueryBuilder
            The LanceQueryBuilder object.
        """
        self._metric = metric
        return self

    def nprobes(self, nprobes: int) -> LanceVectorQueryBuilder:
        """Set the number of probes to use.

        Higher values will yield better recall (more likely to find vectors if
        they exist) at the expense of latency.

        See discussion in [Querying an ANN Index][querying-an-ann-index] for
        tuning advice.

        Parameters
        ----------
        nprobes: int
            The number of probes to use.

        Returns
        -------
        LanceVectorQueryBuilder
            The LanceQueryBuilder object.
        """
        self._nprobes = nprobes
        return self

    def refine_factor(self, refine_factor: int) -> LanceVectorQueryBuilder:
        """Set the refine factor to use, increasing the number of vectors sampled.

        As an example, a refine factor of 2 will sample 2x as many vectors as
        requested, re-ranks them, and returns the top half most relevant results.

        See discussion in [Querying an ANN Index][querying-an-ann-index] for
        tuning advice.

        Parameters
        ----------
        refine_factor: int
            The refine factor to use.

        Returns
        -------
        LanceVectorQueryBuilder
            The LanceQueryBuilder object.
        """
        self._refine_factor = refine_factor
        return self

    def to_arrow(self) -> pa.Table:
        """
        Execute the query and return the results as an
        [Apache Arrow Table](https://arrow.apache.org/docs/python/generated/pyarrow.Table.html#pyarrow.Table).

        In addition to the selected columns, LanceDB also returns a vector
        and also the "_distance" column which is the distance between the query
        vector and the returned vectors.
        """
        vector = self._query if isinstance(self._query, list) else self._query.tolist()
        query = Query(
            vector=vector,
            filter=self._where,
            prefilter=self._prefilter,
            k=self._limit,
            metric=self._metric,
            columns=self._columns,
            nprobes=self._nprobes,
            refine_factor=self._refine_factor,
            vector_column=self._vector_column,
            with_row_id=self._with_row_id,
        )
        return self._table._execute_query(query)

    def where(self, where: str, prefilter: bool = False) -> LanceVectorQueryBuilder:
        """Set the where clause.

        Parameters
        ----------
        where: str
            The where clause which is a valid SQL where clause. See
            `Lance filter pushdown <https://lancedb.github.io/lance/read_and_write.html#filter-push-down>`_
            for valid SQL expressions.
        prefilter: bool, default False
            If True, apply the filter before vector search, otherwise the
            filter is applied on the result of vector search.
            This feature is **EXPERIMENTAL** and may be removed and modified
            without warning in the future.

        Returns
        -------
        LanceQueryBuilder
            The LanceQueryBuilder object.
        """
        self._where = where
        self._prefilter = prefilter
        return self


class LanceFtsQueryBuilder(LanceQueryBuilder):
    """A builder for full text search for LanceDB."""

    def __init__(self, table: "lancedb.table.Table", query: str):
        super().__init__(table)
        self._query = query

    def to_arrow(self) -> pa.Table:
        try:
            import tantivy
        except ImportError:
            raise ImportError(
                "Please install tantivy-py `pip install tantivy@git+https://github.com/quickwit-oss/tantivy-py#164adc87e1a033117001cf70e38c82a53014d985` to use the full text search feature."
            )

        from .fts import search_index

        # get the index path
        index_path = self._table._get_fts_index_path()
        # open the index
        index = tantivy.Index.open(index_path)
        # get the scores and doc ids
        row_ids, scores = search_index(index, self._query, self._limit)
        if len(row_ids) == 0:
            empty_schema = pa.schema([pa.field("score", pa.float32())])
            return pa.Table.from_pylist([], schema=empty_schema)
        scores = pa.array(scores)
        output_tbl = self._table.to_lance().take(row_ids, columns=self._columns)
        output_tbl = output_tbl.append_column("score", scores)
        if self._with_row_id:
            row_ids = pa.array(row_ids)
            output_tbl = output_tbl.append_column("_rowid", row_ids)
        return output_tbl


class LanceEmptyQueryBuilder(LanceQueryBuilder):
    def to_arrow(self) -> pa.Table:
        ds = self._table.to_lance()
        return ds.to_table(
            columns=self._columns,
            filter=self._where,
            limit=self._limit,
        )


class LanceHybridQueryBuilder(LanceQueryBuilder):
    def __init__(self, table: "lancedb.table.Table", query: str, vector_column: str):
        super().__init__(table)
        self.validate()
        self._fts_query = LanceFtsQueryBuilder(table, query)
        query = self._query_to_vector(table, query, vector_column)
        self._vector_query = LanceVectorQueryBuilder(table, query, vector_column)
        self._weight = 0.75
        self._norm = "rank"

    def validate(self):
        if self._table._get_fts_index_path() is None:
            raise ValueError(
                "Please create a full-text search index " "to perform hybrid search."
            )

    def to_arrow(self) -> pa.Table:
        # TODO call to_arrow() on both queries concurrently
        vector_results = self._vector_query.with_row_id(True).to_arrow()
        # rename the _distance column to _score for consistency
        vector_results = vector_results.rename_columns(
            [
                "_score" if name == "_distance" else name
                for name in vector_results.column_names
            ]
        )
        # TODO is fts scores in the right order?
        fts_results = self._fts_query.with_row_id(True).to_arrow()
        fts_results = fts_results.rename_columns(
            [
                "_score" if name == "score" else name
                for name in fts_results.column_names
            ]
        )        

        # convert to ranks if needed
        if self._norm == "rank":
            vector_results = self._rank(vector_results)
            fts_results = self._rank(fts_results)
            fill = max(len(vector_results), len(fts_results)) + 1
        else:
            vector_results = self._normalize_scores(vector_results)
            fts_results = self._normalize_scores(fts_results)
            # TODO fill with what?
            fill = 0.0

        results = self._merge_results(vector_results, fts_results, fill=fill)
        if not self._with_row_id:
            results = results.drop(["_rowid"])
        return results

    def _rank(self, results: pa.Table):
        if len(results) == 0:
            return results
        # Get the _score column from results
        scores = results.column("_score").to_numpy()
        sort_indices = np.argsort(scores)
        ranks = np.empty_like(sort_indices)
        ranks[sort_indices] = np.arange(len(scores)) + 1
        # replace the _score column with the ranks
        _score_idx = results.column_names.index("_score")
        results = results.set_column(
            _score_idx, "_score", pa.array(ranks, type=pa.float32())
        )
        return results

    def _normalize_scores(self, results: pa.Table):
        if len(results) == 0:
            return results
        # Get the _score column from results
        scores = results.column("_score").to_numpy()
        # normalize the scores by subtracting the min and dividing by the max
        max, min = np.max(scores), np.min(scores)
        if np.isclose(max, min):
            rng = max
        else:
            rng = max - min
        scores = (scores - min) / rng
        # replace the _score column with the ranks
        _score_idx = results.column_names.index("_score")
        results = results.set_column(
            _score_idx, "_score", pa.array(scores, type=pa.float32())
        )
        return results

    def _merge_results(
        self, vector_results: pa.Table, fts_results: pa.Table, fill: float
    ):
        # If both are empty then just return an empty table
        if len(vector_results) == 0 and len(fts_results) == 0:
            return vector_results
        # If one is empty then return the other
        if len(vector_results) == 0:
            return fts_results
        if len(fts_results) == 0:
            return vector_results

        # sort both input tables on _rowid
        combined_list = []
        vector_list = vector_results.sort_by("_rowid").to_pylist()
        fts_list = fts_results.sort_by("_rowid").to_pylist()
        i, j = 0, 0
        while i < len(vector_list):
            if j >= len(fts_list):
                for vi in vector_list[i:]:
                    vi["_score"] = self._combine_score(vi["_rowid"], fill)
                    combined_list.append(vi)
                break

            vi = vector_list[i]
            fj = fts_list[j]
            if vi["_rowid"] == fj["_rowid"]:
                vi["_score"] = self._combine_score(vi["_rowid"], fj["_rowid"])
                combined_list.append(vi)
                i += 1
                j += 1
            elif vector_list[i]["_rowid"] < fts_list[j]["_rowid"]:
                vi["_score"] = self._combine_score(vi["_rowid"], fill)
                combined_list.append(vi)
                i += 1
            else:
                fj["_score"] = self._combine_score(fj["_rowid"], fill)
                combined_list.append(fj)
                j += 1
        if j < len(fts_list) - 1:
            for fj in fts_list[j:]:
                fj["_score"] = self._combine_score(fj["_rowid"], fill)
                combined_list.append(fj)
        return pa.Table.from_pylist(combined_list, schema=vector_results.schema)

    def _combine_score(self, score1, score2):
        return self._weight * score1 + (1 - self._weight) * score2

    def rerank(self, weight=0.75, normalize="score") -> LanceHybridQueryBuilder:
        if weight < 0 or weight > 1:
            raise ValueError("weight must be between 0 and 1.")
        if normalize not in ["auto", "rank", "score"]:
            raise ValueError("normalize must be 'auto', 'rank' or 'score'.")
        self._weight = weight
        self._norm = normalize
        return self

    def limit(self, limit: int) -> LanceHybridQueryBuilder:
        self._vector_query.limit(limit)
        self._fts_query.limit(limit)
        return self

    def select(self, columns: list) -> LanceHybridQueryBuilder:
        self._vector_query.select(columns)
        self._fts_query.select(columns)
        return self

    def where(self, where: str, prefilter: bool = False) -> LanceHybridQueryBuilder:
        self._vector_query.where(where, prefilter=prefilter)
        self._fts_query.where(where)
        return self

    def metric(self, metric: Literal["L2", "cosine"]) -> LanceHybridQueryBuilder:
        self._vector_query.metric(metric)
        return self

    def nprobes(self, nprobes: int) -> LanceHybridQueryBuilder:
        self._vector_query.nprobes(nprobes)
        return self

    def refine_factor(self, refine_factor: int) -> LanceHybridQueryBuilder:
        self._vector_query.refine_factor(refine_factor)
        return self
