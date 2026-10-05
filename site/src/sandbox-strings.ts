// The wording of the sandbox page, as approved on 4 October 2026. Change it only with approval.
// The sentences that report what was built and how the past requests fared come from the core.

const rows = (n: number) => `${n.toLocaleString('en-AU')} ${n === 1 ? 'row' : 'rows'}`;

export const sandboxStrings = {
  intro:
    'The sandbox builds a synthetic database in this browser from your catalogue and your inventory, so that you can run SQL against data that has the shape of Clarity and contains no patient information.',

  steps: [
    'Choose the catalogue file and the inventory file.',
    'Build the synthetic database.',
    'Run SQL against the synthetic database.',
    'Run the past requests.',
  ],

  chooseInventory: 'Choose the inventory file:',
  inventoryNote: 'The inventory file is the zip file that Schemalyser produced from your requests.',
  inventoryError:
    'Schemalyser could not read the inventory file. Please check that you have chosen the zip file that Schemalyser produced.',

  buildWhat:
    'Schemalyser builds one table for each table in the inventory, with every column that the catalogue lists for it. It fills the tables with invented rows, and it gives joined columns matching values so that the joins in your requests find rows.',
  rowsLabel: 'Rows in each table:',
  build: 'Build the synthetic database',
  building: 'Schemalyser is building the synthetic database.',
  invented: 'Every value in the synthetic database is invented. A result from the sandbox says nothing about real patients.',
  noValuesYet:
    'The inventory does not yet say which values your requests filter on, so a request that filters on a value may run and return no rows.',

  queryWhat:
    'Write a query in T-SQL, or paste one. Schemalyser will translate it and run it against the synthetic database.',
  run: 'Run the query',
  returned: (n: number) => `The query returned ${rows(n)}.`,
  returnedFirst: (n: number, shown: number) =>
    `The query returned ${rows(n)}, and the first ${shown.toLocaleString('en-AU')} are shown below.`,
  returnedNone: 'The query ran and returned no rows.',
  unreadable: 'Schemalyser could not read the query as T-SQL.',
  unsupported:
    'The sandbox cannot run stored procedures, dynamic SQL or IF blocks. A query that uses them may still be valid on SQL Server.',
  databaseError:
    "The sandbox's database could not run the translated query. The query may still be valid on SQL Server. The database reported:",
  showTranslated: 'Show the translated query',

  offlineOnly:
    'The requests may contain personal information, so Schemalyser will accept the folder only while this computer is offline.',
  runRequests: 'Run the past requests',

  onlyInThisTab:
    'The synthetic database exists only in this browser tab, and Schemalyser discards it when you close the page.',
};
