import type { RecordData as R } from "./api";
export type Common = {
  run: (fn: () => Promise<unknown>, message?: string) => Promise<any>;
  refresh: () => void;
  revision: number;
  reviewer: string;
  busy: boolean;
  modelState: R;
  datasetId: string;
  datasets: R[];
  questions: R[];
  banks: R[];
  bankId: string;
  setBankId: (id: string) => void;
  snapshots: R[];
  jobs: R[];
};
