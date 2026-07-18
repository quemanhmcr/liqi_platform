terraform {
  backend "pg" {}

  encryption {
    state {
      enforced = true
    }
    plan {
      enforced = true
    }
  }
}
