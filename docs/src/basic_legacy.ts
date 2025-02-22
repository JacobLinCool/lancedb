// --8<-- [start:import]
import * as lancedb from "vectordb";
import { Schema, Field, Float32, FixedSizeList, Int32 } from "apache-arrow";
// --8<-- [end:import]
import * as fs from "fs";
import { Table as ArrowTable, Utf8 } from "apache-arrow";

const example = async () => {
  fs.rmSync("data/sample-lancedb", { recursive: true, force: true });
  // --8<-- [start:open_db]
  const uri = "data/sample-lancedb";
  const db = await lancedb.connect(uri);
  // --8<-- [end:open_db]

  // --8<-- [start:create_table]
  const tbl = await db.createTable(
    "myTable",
    [
      { vector: [3.1, 4.1], item: "foo", price: 10.0 },
      { vector: [5.9, 26.5], item: "bar", price: 20.0 },
    ],
    { writeMode: lancedb.WriteMode.Overwrite }
  );
  // --8<-- [end:create_table]

  // --8<-- [start:add]
  const newData = Array.from({ length: 500 }, (_, i) => ({
    vector: [i, i + 1],
    item: "fizz",
    price: i * 0.1,
  }));
  await tbl.add(newData);
  // --8<-- [end:add]

  // --8<-- [start:create_index]
  await tbl.createIndex({
    type: "ivf_pq",
    num_partitions: 2,
    num_sub_vectors: 2,
  });
  // --8<-- [end:create_index]

  // --8<-- [start:create_empty_table]
  const schema = new Schema([
    new Field("id", new Int32()),
    new Field("name", new Utf8()),
  ]);
  const empty_tbl = await db.createTable({ name: "empty_table", schema });
  // --8<-- [end:create_empty_table]

  // --8<-- [start:search]
  const query = await tbl.search([100, 100]).limit(2).execute();
  // --8<-- [end:search]
  console.log(query);

  // --8<-- [start:delete]
  await tbl.delete('item = "fizz"');
  // --8<-- [end:delete]

  // --8<-- [start:drop_table]
  await db.dropTable("myTable");
  // --8<-- [end:drop_table]
};

async function main() {
  await example();
  console.log("Basic example: done");
}

main();
