// One requirement of one file: what the machine concluded, where the original shows it,
// and the person's own review. The review is saved beside the machine result, never over it.
import { Banner, Button, Input, Radio, RadioGroup, Space, Tag, TextArea, Typography } from '@douyinfe/semi-ui';
import { useEffect, useId, useRef, useState } from 'react';
import { OriginalView } from './Evidence';
import { bucket, bucketColor, bucketLabel, judgmentLabel, locationLabel, observation, resultLabel, reviewOf, when } from './model';
import type { Check, Citation, FactQuestion, FactView, Task, TaskFile } from './model';
import { call, newId, PpapError, say } from './ppap';

const { Paragraph, Text, Title } = Typography;

type Props = { task: Task; row: TaskFile; check: Check; position: number; stale: boolean; facts: FactView | null; onBack: () => void; onChanged: () => Promise<void> };

/** The original facts still to confirm for this clause. */
export const openFacts = (facts: FactView | null, check: Check) => (facts?.questions ?? []).filter(question => !question.answered && check.review_item_key && question.item_keys.includes(check.review_item_key));

export function Issue({ task, row, check, position, stale, facts, onBack, onChanged }: Props) {
  const kind = bucket(check);
  const fileOf = (versionId: string) => task.files.find(file => file.file.id === versionId)?.file;
  const sources = check.evidence.filter(item => fileOf(item.file_version_id));
  const [shown, setShown] = useState(0);
  useEffect(() => setShown(0), [check.rule_item.id, row.file.id]);
  const current = sources[shown];
  const viewed = current ? fileOf(current.file_version_id)! : row.file;
  const page = current?.locator && 'page' in current.locator ? current.locator.page : 1;

  return (
    <article className="issue" aria-label="检查项详情">
      <div className="issue-main">
        <Button className="back" theme="borderless" size="small" onClick={onBack}>返回问题列表</Button>
        <Space align="center">
          <Tag color={bucketColor(kind)} type="light">{bucketLabel(kind)}</Tag>
          <Text type="tertiary">第 {position} 条 · {check.rule_item.category}</Text>
        </Space>
        <Title heading={6} className="clause">{check.rule_item.rule_to_check}</Title>
        {stale && <Banner type="warning" fullMode={false} closeIcon={null} description="这是上一版资料的结果。资料已更新，重新审核后以新结果为准。" />}

        <Text strong className="label">判定理由</Text>
        <Paragraph className="pre">{observation(check)}</Paragraph>
        {check.confirmation && (
          <>
            <Text strong className="label">依据人工确认的原件事实</Text>
            <Paragraph className="pre">
              {resultLabel[check.confirmation.status]}。{check.confirmation.suggestions}
              <Text type="tertiary" size="small"> {when(check.confirmation.confirmed_at)} 确认；机器结果仍为“{resultLabel[check.finding?.status ?? 'UNABLE']}”。</Text>
            </Paragraph>
          </>
        )}
        {task.creator.mine && facts?.round_id === task.round_id && openFacts(facts, check).map(question => <FactForm key={question.file_version_id + question.field} roundId={task.round_id!} revision={facts.revision} question={question} onChanged={onChanged} />)}

        <Text strong className="label">原件依据</Text>
        {sources.length === 0 ? (
          <Text type="tertiary">这一条没有可定位的原件位置，请对照原件核对。</Text>
        ) : (
          <RadioGroup type="card" direction="vertical" aria-label="原件依据" value={shown} onChange={event => setShown(Number(event.target.value))}>
            {sources.map((item, index) => (
              <Radio key={index} value={index} extra={item.excerpt || undefined}>
                {fileOf(item.file_version_id)!.original_filename} · {locationLabel(item.locator) || item.location_gap || '位置未记录'}
              </Radio>
            ))}
          </RadioGroup>
        )}
      </div>
      <div className="issue-original">
        <OriginalView source={{ roundId: task.round_id!, file: viewed }} page={page} locator={current?.locator ?? null} />
      </div>
      {check.review_item_key && task.round_id && (
        <div className="issue-review">
          <ReviewForm key={check.review_item_key} roundId={task.round_id} check={check} fileName={fileOf} ownFile={row.file.id} editable={task.creator.mine} onChanged={onChanged} />
        </div>
      )}
    </article>
  );
}

/** The task's creator answers one original fact; PPAP recomputes the clauses that depend on it, without a model. */
function FactForm({ roundId, revision, question, onChanged }: { roundId: string; revision: number; question: FactQuestion; onChanged: () => Promise<void> }) {
  const id = useId();
  const [value, setValue] = useState<boolean | string | null>(question.answer_type === 'boolean' ? null : '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  // One operation per answer, kept across retries so a lost reply never answers twice; a definite
  // refusal (a stale question, say) is settled, so the next attempt is a new operation.
  const operation = useRef(newId());
  const ready = question.answer_type === 'boolean' ? typeof value === 'boolean' : typeof value === 'string' && value.trim().length > 0;

  async function submit() {
    setBusy(true);
    setNotice('');
    try {
      await call(`/audit-rounds/${roundId}/fact-answers`, {
        method: 'POST',
        body: { operation_id: operation.current, expected_revision: revision, file_version_id: question.file_version_id, field: question.field, expected_question_fingerprint: question.fingerprint, value: typeof value === 'string' ? value.trim() : value },
      });
      void window.JSSDK.toast.success('已确认，相关条款已按原件事实重新判定');
      await onChanged();
    } catch (failure) {
      if (failure instanceof PpapError && failure.status >= 400 && failure.status < 500) operation.current = newId();
      setNotice(say(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="fact" aria-labelledby={`${id}-prompt`}>
      <Text strong className="label" id={`${id}-prompt`}>需要确认的原件事实</Text>
      <Text>{question.prompt}</Text>
      {question.locator && <Text type="tertiary" size="small">原件位置：{locationLabel(question.locator)}</Text>}
      {notice && <Banner type="warning" fullMode={false} closeIcon={null} description={notice} />}
      {question.answer_type === 'boolean' ? (
        <RadioGroup aria-labelledby={`${id}-prompt`} value={typeof value === 'boolean' ? String(value) : undefined} disabled={busy} onChange={event => setValue(event.target.value === 'true')}>
          <Radio value="true">是</Radio>
          <Radio value="false">否</Radio>
        </RadioGroup>
      ) : (
        <Input aria-labelledby={`${id}-prompt`} value={typeof value === 'string' ? value : ''} maxLength={2000} disabled={busy} onChange={setValue} />
      )}
      <div>
        <Button theme="solid" type="primary" disabled={!ready} loading={busy} onClick={() => void submit()}>确认事实</Button>
      </div>
    </section>
  );
}

const judgments = ['confirm', 'disagree', 'unknown', 'manual-satisfied', 'manual-unsatisfied'] as const;

function ReviewForm({ roundId, check, fileName, ownFile, editable, onChanged }: { roundId: string; check: Check; fileName: (versionId: string) => { original_filename: string } | undefined; ownFile: string; editable: boolean; onChanged: () => Promise<void> }) {
  const [where, setWhere] = useState('');
  const [quote, setQuote] = useState('');
  const saved = reviewOf(check);
  const id = useId();
  const [editing, setEditing] = useState(!saved && editable);
  const [judgment, setJudgment] = useState(saved?.judgment || '');
  const [rationale, setRationale] = useState(saved?.rationale || '');
  const [nextStep, setNextStep] = useState(saved?.next_step || '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    // A newer saved review replaces what is on screen unless the person is typing.
    if (!editing) {
      setJudgment(saved?.judgment || '');
      setRationale(saved?.rationale || '');
      setNextStep(saved?.next_step || '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved?.version]);

  // A manual verdict must cite the original; the evidence already on screen is that citation.
  const cited: Citation[] = check.evidence
    .filter(item => item.locator && item.excerpt.trim() && fileName(item.file_version_id))
    .map(item => ({ file_version_id: item.file_version_id, location: locationLabel(item.locator), excerpt: item.excerpt.trim().slice(0, 4000) }))
    .slice(0, 20);
  const manual = judgment.startsWith('manual-');
  // Without a located result, the reviewer cites the original themself: where, and what it says.
  const own: Citation[] = where.trim() && quote.trim() ? [{ file_version_id: ownFile, location: where.trim(), excerpt: quote.trim() }] : [];
  const citations = cited.length ? cited : own;
  const valid = Boolean(judgment) && rationale.trim().length > 0 && rationale.trim().length <= 2000 && nextStep.trim().length <= 2000 && (!manual || citations.length > 0);

  async function submit() {
    setBusy(true);
    setNotice('');
    try {
      await call(`/rounds/${roundId}/reviews`, {
        method: 'PUT',
        body: {
          item_key: check.review_item_key,
          judgment,
          rationale: rationale.trim(),
          next_step: nextStep.trim(),
          version: saved?.version ?? 0,
          ...(manual ? { citations } : saved?.citations?.length ? { citations: saved.citations } : {}),
        },
      });
      setEditing(false);
      await onChanged();
      void window.JSSDK.toast.success('复核已保存');
    } catch (failure) {
      setNotice(say(failure));
      if (failure instanceof PpapError && failure.code === 'review_conflict') {
        setEditing(false);
        await onChanged().catch(() => undefined);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!editing && !saved) return null;
  if (!editing && saved)
    return (
      <section className="review" aria-label="人工复核">
        <Text strong className="label">人工复核</Text>
        {notice && <Banner type="warning" fullMode={false} closeIcon={null} description={notice} />}
        <div><Tag color="blue" type="light">{judgmentLabel[saved.judgment] || saved.judgment}</Tag></div>
        <Paragraph className="pre">{saved.rationale}</Paragraph>
        {saved.next_step && <Paragraph className="pre" type="tertiary">下一步：{saved.next_step}</Paragraph>}
        {editable && <div><Button size="small" onClick={() => setEditing(true)}>修改复核</Button></div>}
      </section>
    );

  return (
    <section className="review" aria-label="人工复核">
      <Text strong className="label">人工复核</Text>
      {notice && <Banner type="warning" fullMode={false} closeIcon={null} description={notice} />}
      <RadioGroup direction="vertical" aria-label="人工判断" value={judgment} disabled={busy} onChange={event => setJudgment(String(event.target.value))}>
        {judgments.map(value => (
          <Radio key={value} value={value}>
            {judgmentLabel[value]}
          </Radio>
        ))}
      </RadioGroup>
      {manual && cited.length === 0 && (
        <>
          <Text type="tertiary" size="small">这一条没有可定位的原件位置。人工确认需要写明你依据的位置和原文。</Text>
          <label htmlFor={`${id}-where`}>原件位置</label>
          <Input id={`${id}-where`} value={where} maxLength={4000} disabled={busy} onChange={setWhere} placeholder="例如：工作表 PFD 第 12 行" />
          <label htmlFor={`${id}-quote`}>原文摘录</label>
          <TextArea id={`${id}-quote`} value={quote} maxLength={4000} autosize={{ minRows: 1, maxRows: 4 }} disabled={busy} onChange={setQuote} />
        </>
      )}
      <label htmlFor={`${id}-why`}>理由</label>
      <TextArea id={`${id}-why`} value={rationale} maxLength={2000} autosize={{ minRows: 2, maxRows: 8 }} disabled={busy} onChange={setRationale} placeholder="说明你的判断依据" />
      <label htmlFor={`${id}-next`}>下一步（选填）</label>
      <TextArea id={`${id}-next`} value={nextStep} maxLength={2000} autosize={{ minRows: 1, maxRows: 6 }} disabled={busy} onChange={setNextStep} placeholder="例如：请供应商更正后重新提交" />
      <Space>
        <Button theme="solid" type="primary" disabled={!valid} loading={busy} onClick={() => void submit()}>保存复核</Button>
        {saved && <Button disabled={busy} onClick={() => setEditing(false)}>取消</Button>}
      </Space>
    </section>
  );
}
