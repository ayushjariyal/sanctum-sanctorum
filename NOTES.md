# Notes

## Live app

**https://sanctum-sanctorum-ao8y.onrender.com/**

Swagger docs are at `/docs`. It's on Render's free tier with a Neon Postgres behind it.

Heads up: if nobody's hit it in a while, the first request takes 30-60 seconds while
Render wakes the service up. It's not broken, just cold.

The database seeds itself on first boot, so you can sign straight in with any of these:

| id | name | tier | good for |
|----|------|------|----------|
| 1 | Wong Li | supreme | 15% off, unlimited loans, can access restricted books |
| 2 | Christine Palmer | master | 10% off, 5 loans |
| 3 | Jonathan Pangborn | adept | 5% off, 3 loans |
| 4 | Sara Lin | apprentice | shows the loan limit and the 403 on restricted books |

Member 4 is the interesting one if you want to see things get refused.

## What's done

All 202 tests pass. Everything in SPEC.md is implemented.

Pytest prints two warnings. They come from inside Starlette's `TestClient`, not from my
code, so I left them alone instead of adding a `filterwarnings` rule. Muting third-party
warnings is how you end up missing a real one later.

I didn't do any of the optional extras - no `GET /members`, no extra tests, and the
concurrency one I have opinions about further down.

## How I split things up

The rule I stuck to: if a check doesn't need the database, it's a 422 in `schemas.py`.
If it needs a query, it's a service.

So the ISBN checksum, email format, empty `items`, and the same book twice in one order
are all schema-level. Duplicate ISBN across the catalogue, stock, and loan limits are all
services.

That turned out to matter more than I expected. SPEC.md wants 422 checked before 404, and
there's a test that posts a missing member *and* a duplicated book expecting 422. FastAPI
validates the body before my endpoint function even runs, so I got that ordering for free
instead of having to sequence it by hand.

## Orders never half-apply

`create_order` does everything in phases: load every entity, then check permissions, then
check all the stock, and only then start changing things. Nothing decrements one book and
then discovers a problem with the next, so there's nothing to roll back.

I think that's the right way round. You can get the same result with a try/except and a
rollback, but then the guarantee depends on remembering to catch. This way it's just the
shape of the function.

`autoflush=False` on the session helps here too - in-memory changes don't get written out
early by some unrelated query - and one `commit()` covers the stock changes and the order
insert together. `create_loan` works the same way.

## Duplicate ISBNs and emails

`commit_or_conflict` in `services/common.py` commits and turns an `IntegrityError` into a
409.

I wrote it the obvious way first - `SELECT` to see if the row exists, then raise. It reads
better, but it's racy: two requests both check, both see nothing, both insert, and one of
them gets an unhandled `IntegrityError` and a 500 instead of the 409 you meant. The unique
index is the only thing that actually enforces uniqueness, so I let it. It's also one
query instead of two.

The downside is that catching `IntegrityError` is blunt - it'd also swallow a NOT NULL or
FK violation. I checked what each insert can actually violate and left a comment at both
call sites saying so, so it gets looked at again if anyone adds a second unique column.

I wrote this inline for books and only pulled it out into a helper when members needed the
same five lines. `b8694db` then `e15d8b6` if you want to see that.

## Emails get normalised before they're stored

Stripped and lowercased on the way in, then validated. The order matters both ways -
validating first rejects `" wong@sanctum.org "` which is fine, and stripping only touches
the ends so `"wong strange@..."` still gets rejected.

The reason it's normalised on write rather than compared case-insensitively on read is
that the unique index on `members.email` compares bytes. If mixed-case values could get
in, `Wong@...` and `wong@...` would both insert happily and the constraint would be
lying. Normalising at the edge means the index and the business rule agree.

RFC 5321 actually says the local part is case-sensitive, so this is technically wrong. No
real mail provider behaves that way and the tests want the collision, so I went with it.

## Stored vs computed

This came up three times and I got it different each time on purpose:

`unit_price_cents` on an order item is frozen at purchase. An order is a record of what
happened; it shouldn't re-price itself when someone edits the catalogue.

`due_at` is stored, not derived from `borrowed_at + 14 days`. If the loan period ever
changed, deriving it would silently move the due date on books already out - including
overdue ones that have fees on them. Storing it also lets the overdue queries filter in
SQL.

Loan `status` is *not* stored, because a loan goes from active to overdue without any
request touching it. Time alone changes the answer. A column would be stale immediately
and would need a cron job to keep honest, so it's computed in `loans.loan_status` at read
time.

Related: the top-books report joins `Book` for the title, so it shows the *current* title
even though the price on the same order item is frozen. Opposite calls, both deliberate.

Same thinking behind not adding an `is_returned` boolean. `returned_at IS NULL` is the
only definition of unreturned anywhere in the codebase. Two fields for one fact is two
fields that can disagree.

## Stats and reports are aggregate SQL

`get_member_stats` and `top_books` use COUNT/SUM/GROUP BY rather than walking
relationships in Python. They're both the kind of endpoint you'd poll for a dashboard, and
the Python version loads every order, order item and loan a member has ever had to produce
six integers.

Watch out for `SUM` over zero rows returning `NULL` rather than 0 - that's what the
`coalesce` calls are for. Without them a brand new member's first stats request is a 500.

## Stuff I'm not happy about

**The overdue check exists twice.** Python (`now > loan.due_at`) in `loan_status`, SQL
(`due_at < now`) in member stats. I couldn't share it because `services/loans.py` already
imports from `services/members.py`, so importing back would be circular - and that
direction is the right one, loans are a thing members do. Loading every loan into Python
to dedupe it would have killed the aggregate query too.

The proper fix is a `hybrid_method` on the model, which SQLAlchemy can render as both
Python and SQL. I ran out of time. There's a comment at the SQL site pointing at the other
one.

**Two orders can both grab the last copy.** Both read `stock = 1`, both succeed, stock
goes negative. This was on the optional list and I didn't get to it. The fix that works on
SQLite and Postgres is `UPDATE books SET stock = stock - :qty WHERE id = :id AND stock >=
:qty` and then checking `rowcount` - if it's 0 someone beat you to it. I wouldn't put the
current version in front of real customers.

**`cancel_order` does an N+1.** `item.book.stock += ...` lazy-loads a book per line. Three
items is four queries. Cancellations are rare and orders are small so I left it readable,
but I did notice.

**No migrations.** The schema comes from `create_all()` at startup. Fine for a seeded demo
DB, but the first column change in production has nowhere to go. Real version needs
Alembic.

## Deployment

Render plus Neon.

Render because FastAPI wants a normal long-running process and the `lifespan` handler that
creates tables and seeds just works as written. Vercel would have meant writing an ASGI
shim and rethinking startup for no real benefit. Neon rather than Render's own Postgres
because Render's free database expires after 30 days and this needs to still be up when
you look at it.

Two things in `db.py` had to change and neither was obvious until it fell over:

`connect_args={"check_same_thread": False}` is SQLite-only. Hand it to psycopg and it
raises at import - the app doesn't even start. It's conditional now.

Hosted providers give you either `postgres://`, which SQLAlchemy 2 rejects outright, or
`postgresql://`, which quietly resolves to psycopg2 - a driver that isn't installed here.
Both get rewritten to `postgresql+psycopg://`.

I added `pool_pre_ping=True` as well, because hosted Postgres drops idle connections and
otherwise the first request after a quiet spell dies on a stale one.

The driver lives in a separate `postgres` dependency group, not the main deps. `uv sync`
installs `dev` and not `postgres`, so psycopg isn't even present in my local venv - which
means the test suite can't accidentally depend on it. `uv run pytest` runs on SQLite with
nothing external, as required. I read "don't add new dependencies" as being about the
app's runtime deps, which haven't changed; this is deployment plumbing.

`SANCTUM_DATABASE_URL` is set in Render's dashboard and nowhere else. It's not in any file.

## Spec things I wasn't sure about

**Case-sensitive sorting being unspecified has a bigger effect than it looks.** SQLite and
Postgres disagree on collation, so book listing and the top-books tie-break genuinely
order differently on my laptop and on the deployed app. The spec allows it and I passed it
through to the database rather than forcing `lower()`, but it does mean local and
production can disagree and both be correct. Seemed worth flagging before someone reports
it as a bug.

**`active_loans` and `overdue_loans` overlap.** Active includes overdue, so they don't add
up to a total. The spec does say this clearly, but "active" reading as "active, not
overdue" is an easy wrong assumption and the naming invites it.

**Restricted books being gated the same way for buying and borrowing** is what the spec
says and what I built, but it's odd as a product rule. Not lending out a rare copy makes
sense. Refusing to sell someone a book they could buy anywhere else is stranger.

## AI usage

I used Claude (Claude Code) for most of this - mainly writing the implementations and
talking through decisions.

The way I worked was one logical change at a time. Before accepting anything I had it
explain what the tests were actually pinning and why it picked that approach, then I ran
the suite and committed it myself. The history reflects that, including `b8694db` ->
`e15d8b6` where the duplicate-key handling got written inline first and only extracted
once there were two callers. I did the deployment myself - Neon project, Render service,
env config, checking it actually worked from outside.

Two places it got things wrong:

**It broke `pyproject.toml`.** I'd already added the `postgres` dependency group myself.
When I asked it to make the change it patched the file without re-reading it first and
appended a second identical `postgres = [...]` block. Duplicate key, invalid TOML, would
have blown up on the next `uv lock`. It only spotted it when it printed the file
afterwards. Takeaway: if it's editing a file it hasn't just read, it's working from memory
of what it thinks is in there, and that needs checking.

**It wrote error messages that were wrong, and the tests hid it.** Two of them said "Only
1 copies of 'Darkhold' are available" and "Tier 'apprentice' may hold 1 loans at a time".
Both broken in the most common case - the last copy of a book, and the apprentice tier
whose limit is literally 1. Nothing caught it because every test checks status codes and
none of them look at message text, and the frontend puts `detail` straight into a toast.
I found it reading the code as prose with the suite already green. Fixed in `d8108fb` by
rewording so the number isn't sat next to a countable noun, rather than sticking
pluralisation logic in a service.

The pattern I noticed: it was solid on mechanical work and genuinely useful when I asked
it to justify a decision, and unreliable about anything the tests didn't check. Green
tests were the floor, not proof it was right.
