# A note to send with the first ask

This is the note that a clinician sends to the analytics team with the first query. The page shows the same text with a button that copies it. The places in square brackets are for the sender to fill in. Every name in it is generic.

---

Subject: A request for help with an approved anaesthesia audit, in short steps

Hello,

I am running an approved audit, under the approval [approval reference], and I would like your help to answer it from the source database. The audit asks [the question in one sentence]. It is also the first step towards an OMOP anaesthesia layer, so the answers that you give me will be used again when that layer is built.

The help that I need comes in short steps, and I will send each one only after the one before it.

1. One query that reads only SQL Server's own records. It lists the columns of the tables that our existing anaesthesia queries already read, and the number of rows that the server records for each, rounded down to the nearest ten. It reads no table.
2. A few questions that you may be able to answer from what you know, such as whether two columns join and which codes mean a mean arterial pressure, and, where a question cannot be answered that way, a few short counting queries.
3. The audit query itself, or a one-page specification of it if you would rather write it yourself.

Each query is a single SELECT that writes, creates and changes nothing. It reads WITH (NOLOCK), which means that it takes no row locks, but it holds a schema lock while it runs, so please do not run it during the nightly load. Each counting query rounds its counts down to the nearest ten and leaves out anything that fewer than ten rows hold, and a query on a large table reads a sample of about five million rows, so its counts are estimates. Each query has a comment at the top that says what it does, so that you can read it before you run it. The rounding and the leaving out of small counts reduce what a count can disclose, but they do not make the results anonymous, and repeated counts over slightly different groups can reveal more than one count does. The results are therefore for use inside the hospital until the hospital's own rules say otherwise.

You would paste each result back to me. I put the results into a page that runs in my browser on a hospital computer, with that browser tab taken offline so that the page cannot send anything anywhere. The results, and the facts that you confirm, are kept in a repository that the hospital controls, [the repository].

The queries are written by a tool that I built with the help of an AI model, [the model and the service]. These controls can be checked: the model worked only from invented examples and never saw any hospital data, any of your team's SQL or any name from our database; the tool runs offline; and its code is open to read. The use of AI in this work follows [the hospital's AI policy].

Thank you for considering it. I am glad to go through any of it with you in person.
