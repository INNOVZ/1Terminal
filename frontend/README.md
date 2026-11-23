This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

Set `API_BACKEND_URL` as a server-side environment variable to the backend's verified
HTTPS origin. There is no production default host. Production requests fail
with 503 before any upstream call if the URL is missing, invalid or HTTP; upstream
redirects are refused. Public API reads use the same server transport boundary to
protect both market-data integrity and operator credentials.
Local development defaults to `http://127.0.0.1:8000`.

Set the same strong `ATLAS_OPERATOR_TOKEN` on the backend and the frontend server.
Never use a `NEXT_PUBLIC_*` variable for the token. Authorized controls require an
operator session or explicit Bearer authentication. Verify TLS and the production
environment separately before deployment; a successful build does not verify them.

After a backend restart, saved episodes remain private and `/readyz` returns 503
until a successful live scan revalidates publication. The API reports
`startup_revalidation_required`; live and chart routes both withhold saved candidates.
The scheduler or first live request initiates verification. An empty successful scan
is a valid result, distinct from a failed scan.

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
# alfa-t
