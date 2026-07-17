# Insider trading scrape

In this assignment, I web scrape the EDGAR site to get data and documents on the insider trading happening in 2026, using the most recent filings.

I do the scraping in Python, using the requests library to download each page and BeautifulSoup to read the HTML and XML and pull out the fields I want.

Here is what this scraper does, step by step:

* I start from the SEC EDGAR "latest filings" page, set to show the 100 most recent Form 4 filings.
* That page lists each filing in a small table, so I first pull every field from the table into a dictionary that includes who filed, the company, the form type, the dates, the accession number, and the links. Some of these fields are not in their own neat spot on the page. For example, the filer's name, ID number and role all sit together in one line of text. To pull those apart, I use regular expressions, which are a way of matching text patterns.
* Then I follow each filing's link to its own page and open the Form 4 XML. The XML is the machine-readable version of the filing that SEC stores behind every Form 4, where each piece of information sits in its own labelled tag, so it is much cleaner to pull data from than the web page. From it I get the shares bought or sold, the price, whether it was an acquisition or a disposal, how many shares the person holds afterwards, and the footnotes (the actual text of the filing).
* Not every filing has every field. An issuer row has no file number, and some filings have no footnotes. So I only save a field when it actually exists. This keeps the scraper flexible, so the rows can have slightly different columns without it breaking.
* To avoid re-scraping filings I already have, I make a hash (a short fingerprint) of each filing. If the fingerprint matches what I saved before, I skip it. If it is new or changed, I scrape it.
* Everything is saved to `data/sec_form4.json`. I also keep a change log (what was added, deleted, or modified) and an error log (any pages that failed), each in its own folder.
* Because SEC only shows the latest 100 at a time, I run this regularly and the new filings build up in the same file over time, growing to 500 to 1,000 or more rows.
* I follow SEC's rules by identifying myself in the request header and pausing briefly between requests.
