/** Client state isolation only. Database RLS remains the authorization boundary. */
export interface AccountTicket { userId: string | null; epoch: number }

export class AccountScope {
  private userId: string | null = null;
  private epoch = 0;

  setIdentity(userId: string | null): boolean {
    if (this.userId === userId) return false;
    this.userId = userId;
    this.epoch += 1;
    return true;
  }

  capture(): AccountTicket { return { userId: this.userId, epoch: this.epoch }; }

  isCurrent(ticket: AccountTicket): boolean {
    return ticket.userId === this.userId && ticket.epoch === this.epoch;
  }
}
