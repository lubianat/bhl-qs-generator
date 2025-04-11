
This is a little tool inspired by Rod's [BHL to Wikidata](https://bhl2wiki.herokuapp.com) and with exactly the same goal. 

The key differences are:

- It is built in Python/Flask

- It looks for the authors that have been reconciled to BHL ids before writing the Quickstatements

- It uses "[written work](https://www.wikidata.org/wiki/Q47461344)" to try and have a coverage of the many kinds of titles in BHL

- It provides a little API (/api/quickstatements) so it is possible to integrate this into semi-automatic workflows

- It uses `mul`, the multilingual code for the title. 
