# How I Built Insider Ledger: A Methodology

*By Dimuthu Attanayake*

## Introduction

When a director, an executive, or anyone who owns more than ten percent of a public company buys or sells shares in their own company, U.S. law requires them to tell the government within two business days. The form they file is called a Form 4, and it goes into a public database run by the Securities and Exchange Commission called EDGAR. This is what people usually mean when they say "insider trading" — not the illegal kind, but the legal, disclosed kind that happens every day.

Insider Ledger is my attempt to make that disclosure actually readable. The SEC publishes every Form 4, but the raw filings are scattered, one per page, written in regulatory language, and impossible to browse in bulk unless you know your way around EDGAR. I built a scraper that collects these filings continuously, a database that organizes them into individual trades, and a public website where anyone can search them, filter them, and download them — with each trade priced against what the stock was actually worth on the market that day.

The argument of the data, once you can see it in aggregate, is a surprising one: most "insider trading" is not insiders betting on their companies at all. It is payday. Grants, stock awards, and option exercises — the routine machinery of executive compensation — make up the bulk of the filings. The rare and interesting event is an insider spending their own cash on the open market.

## Sources

My primary source is the SEC's EDGAR "current events" feed, which lists the 100 most recent Form 4 filings at any moment. The data is gathered by the SEC itself, but it originates with the insiders: each filing is prepared and signed by (or on behalf of) the person doing the trading, under legal penalty for false statements. So the data's point of view is the insider's own — reviewed by lawyers, structured by the SEC, and mandated by Congress.

My second source is Yahoo Finance, which I used to get each stock's daily closing price. This lets me compare what an insider paid per share with what the market said the share was worth at the end of that same day.

## Scope

The dataset covers Form 4 filings that passed through EDGAR's latest-100 feed between July 5 and July 17, 2026, collected by a scraper that ran automatically every 30 minutes. At the time of writing that is 1,532 unique filings containing 2,003 individual transactions, across 314 companies and around 700 insiders.

This is a rolling window, not a census. If more than 100 filings arrived between two runs of my scraper — which can happen in the after-market rush when most Form 4s get filed — some filings would pass through the feed without being captured. My dataset is best described as a large, continuous sample of recent insider filings, not the complete record. The complete record exists on EDGAR; my contribution is making a live slice of it explorable.

## Gathering and preparation

I wrote the scraper in Python, using the requests library to download pages and BeautifulSoup to parse them. It works in two layers. The first layer reads the feed's table and captures every visible field: who filed, for which company, when, and the accession number that uniquely identifies the filing. The second layer follows each filing to its index page and then to the raw Form 4 XML, where the actual substance lives: how many shares, at what price, whether they were acquired or disposed of, what the insider held afterward, and the footnotes where the caveats hide.

The scraper runs on GitHub Actions every 30 minutes and commits its results to a public repository. Because Form 4s are immutable once filed, I detect changes by hashing each filing's stable identifiers (accession number, acceptance time, and filer ID); unchanged filings are skipped rather than re-downloaded. Every run writes a changelog of additions and an error log of anything that failed. The SEC asks scrapers to identify themselves, so every request carries my name and Columbia email, and the code pauses between requests to stay well under the SEC's rate limit.

A separate build script turns the accumulated JSON into a SQLite database with two tables — one row per filing, one row per transaction — and joins each transaction to Yahoo's closing price by ticker and date. A Flask app on Render serves the search interface, the filters, the CSV export, and the dashboard.

## Definitions

A few definitions do a lot of work in this project. A "trade" is a single transaction line inside a filing; one filing can contain several. Each transaction carries an SEC code, which I translated into plain English: P is an open-market purchase, S an open-market sale, A a grant or award, M and X are option exercises, F is shares withheld for taxes. I group A, M, X, and F together as "routine pay," because that is what they are — compensation events, not market decisions.

"Vs. market" is the percentage gap between the price the insider paid and that day's closing price. "Paper profit" on the dashboard is that gap multiplied by the number of shares — what the acquired shares were worth at the close, minus what the insider paid for them. I call it paper profit deliberately: nothing says the insider sold, and most of these gains are simply compensation being valued, not trading skill.

## Critique: what the data cannot say

The most important limitation is interpretive: a Form 4 records a transaction, not a motive. An insider selling can be diversifying, divorcing, or paying tuition. The site's footer says "a trade is not proof of intent," and I mean it.

There are also mechanical limits. Grants and option exercises are often priced at $0 or at an old strike price, so price-versus-market comparisons are only meaningful for real cash trades — I compute them only where they make sense. Foreign companies traded as ADRs sometimes report prices in a different share class or currency, which produces gaps that look dramatic but are artifacts. Some filers report no ticker at all (the literal ticker "NONE" appears 19 times). Yahoo has no closing price for weekends, holidays, or thinly traded symbols, so only about half of the trades carry a market comparison. And the rolling-window problem above means busy filing days are undersampled.

## Verification

I verified the pipeline in three ways. First, spot checks: rows from my database against the original filings on EDGAR, which every row links back to. Second, the pipeline audits itself — every run logs errors and changes, and those logs led me to real bugs, including one where an unusual HTML structure in the feed crashed the scraper intermittently for five days until I found and fixed it. Third, the price join is verifiable by anyone: the tickers, dates, and prices are all public, and the CSV export means a reader can rerun my comparisons themselves.

## Findings

Out of 2,003 transactions, about 44 percent are routine pay, about a third are open-market sales, and only 7 percent — roughly one trade in fourteen — is an insider buying their own stock with their own money on the open market. That inversion is the story: the phrase "insider trading" conjures conviction bets, but the disclosure system mostly documents compensation. The dashboard leads with the exceptions, because they are the signal: the largest cash purchases and the insiders who came out furthest ahead against the market.

## Conclusion

What we know now is modest but real: what two weeks of American insider filings actually look like, searchable by anyone, with every claim traceable to a government document. What more could be done is clear too — run the scraper for a year and seasonal patterns emerge; join it to news events and you could ask whether buys cluster before announcements. Everything here is public by law and by design: Congress decided in 1934 that this belongs in the open. My project just makes the reading easier.

*The code, data, and this methodology are public at github.com/DimuthuAttanayake/form4-search-app.*
