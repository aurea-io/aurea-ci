/**
 * Aurea Governance Validator
 * Validates PR & Issue metadata, generates Step Summaries,
 * and posts helpful diagnostic comments on PRs when validation fails.
 */

const ALLOWED_AREA_REGEX = /^area:/i;
const ISSUE_REF_REGEX =
  /\b(?:close[sd]?|fix(?:e[sd]?)?|resolve[sd]?)\s+(?:(?:https?:\/\/github\.com\/[^/\s]+\/[^/\s]+\/issues\/)|(?:[^/\s#]+\/[^/\s#]+#))?#?\d+\b/i;

const REPORT_MARKER = '<!-- aurea-governance-report -->';

export async function validateGovernance({ github, context, core }) {
  const isPR = context.eventName === 'pull_request';
  const payload = isPR ? context.payload.pull_request : context.payload.issue;
  const currentLabels = payload.labels || [];
  const matchingAreas = currentLabels.filter((label) => ALLOWED_AREA_REGEX.test(label.name));
  const hasValidArea = matchingAreas.length === 1;

  const body = payload.body || '';
  const hasIssueRef = isPR ? ISSUE_REF_REGEX.test(body) : true;

  if (hasValidArea && hasIssueRef) {
    console.log('✅ Metadatos de gobernanza conformes con la normativa de Aurea.');
    return;
  }

  const errors = [];
  if (!hasValidArea) {
    if (matchingAreas.length === 0) {
      errors.push('Falta una etiqueta de área obligatoria (`area:<section>.<page>` o área transversal `area:<nombre>`).');
    } else {
      errors.push(
        `El ítem tiene múltiples etiquetas de área (${matchingAreas.map((l) => l.name).join(', ')}). Debe tener exactamente una.`
      );
    }
  }

  if (!hasIssueRef) {
    errors.push(
      'El cuerpo del PR debe incluir una referencia de cierre a un issue existente (ej: `Closes #123` o `Resolves aurea-io/repo#123`).'
    );
  }

  const reportMarkdown = buildReportMarkdown(isPR, errors);

  if (core.summary) {
    await core.summary.addRaw(reportMarkdown).write();
  }

  if (isPR && github) {
    await postOrUpdatePRComment(github, context, reportMarkdown);
  }

  core.setFailed(errors.join(' | '));
}

function buildReportMarkdown(isPR, errors) {
  return [
    `## ❌ Error de Gobernanza en ${isPR ? 'Pull Request' : 'Issue'}`,
    '',
    '> [!WARNING]',
    '> No se cumplen los requisitos mínimos de gobernanza y metadatos de Aurea.',
    '',
    '### 🔍 Motivo del rechazo:',
    ...errors.map((e) => `- 🔴 **${e}**`),
    '',
    '---',
    '',
    '### 💡 ¿Cómo corregirlo?',
    '',
    '1. **Asigná exactamente una etiqueta de área válida:**',
    '   - Funcionales: `area:commerce.catalog`, `area:commerce.orders`, `area:services.bookings`, `area:gastronomy.tables`, etc.',
    '   - Transversales: `area:cross`, `area:ci`, `area:docs`, `area:auth`, `area:platform.tenants`, etc.',
    '2. **Referencia de Issue (en PR):**',
    '   - Añadí en la descripción del PR una línea como `Resolves #<numero-issue>` o `Closes aurea-io/<repo>#<numero-issue>`.',
    '',
    '📖 **Referencia:** Consulta [`taxonomy/structure.json`](https://github.com/aurea-io/aurea-docs/blob/main/docs/modules-dynamic/taxonomy/structure.json) y [`taxonomy/area.json`](https://github.com/aurea-io/aurea-docs/blob/main/docs/modules-dynamic/taxonomy/area.json).',
    '',
    REPORT_MARKER,
  ].join('\n');
}

async function postOrUpdatePRComment(github, context, reportMarkdown) {
  try {
    const prNumber = context.payload.pull_request.number;
    const { owner, repo } = context.repo;

    const { data: comments } = await github.rest.issues.listComments({
      owner,
      repo,
      issue_number: prNumber,
    });

    const existing = comments.find((c) => c.body && c.body.includes(REPORT_MARKER));
    if (existing) {
      await github.rest.issues.updateComment({
        owner,
        repo,
        comment_id: existing.id,
        body: reportMarkdown,
      });
      console.log(`💬 Comentario de diagnóstico de gobernanza actualizado en PR #${prNumber}`);
    } else {
      await github.rest.issues.createComment({
        owner,
        repo,
        issue_number: prNumber,
        body: reportMarkdown,
      });
      console.log(`💬 Comentario de diagnóstico de gobernanza publicado en PR #${prNumber}`);
    }
  } catch (err) {
    console.warn('⚠️ No se pudo publicar el comentario en el PR:', err.message);
  }
}
